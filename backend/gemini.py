"""Small, dependency-free adapter for the Gemini Developer API."""
import base64
import copy
import json
import mimetypes
import re
import time

import requests


MODEL_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')


def normalize_model(model):
    value = (model or '').strip().removeprefix('models/')
    if not value or not MODEL_PATTERN.fullmatch(value):
        raise ValueError('Chọn một model Gemini hợp lệ trước khi chạy AI.')
    return value


def compatible_schema(schema):
    """Inline refs and keep a compact schema accepted by Gemini.

    Gemini can reject a valid response schema with INVALID_ARGUMENT when its
    constraint graph is too complex. Pydantic validates all bounds again after
    generation, so the wire schema only needs shape, scalar types and enums.
    """
    definitions = copy.deepcopy(schema.get('$defs', {})) if isinstance(schema, dict) else {}
    unsupported = {
        'default', 'examples', 'title', 'minLength', 'maxLength', 'pattern',
        'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum',
        'multipleOf', 'minItems', 'maxItems', 'const',
    }

    def visit(value, resolving=frozenset()):
        if isinstance(value, list):
            return [visit(item, resolving) for item in value]
        if not isinstance(value, dict):
            return value
        reference = value.get('$ref')
        if isinstance(reference, str) and reference.startswith('#/$defs/'):
            name = reference.removeprefix('#/$defs/')
            if name in definitions and name not in resolving:
                expanded = copy.deepcopy(definitions[name])
                expanded.update({key: child for key, child in value.items() if key != '$ref'})
                return visit(expanded, resolving | {name})
        properties = value.get('properties')
        result = {}
        for name, child in value.items():
            if name in unsupported or name == '$defs':
                continue
            if name == 'properties' and isinstance(child, dict):
                result[name] = {key: visit(item, resolving) for key, item in child.items()}
            else:
                result[name] = visit(child, resolving)
        if isinstance(properties, dict) and isinstance(result.get('required'), list):
            result['required'] = [name for name in result['required'] if name in properties]
        return result

    return visit(copy.deepcopy(schema))


def request_body(prompt, images, schema=None):
    parts = [{'text': prompt}]
    for path in images:
        mime = mimetypes.guess_type(str(path))[0] or 'image/jpeg'
        parts.append({'inlineData': {'mimeType': mime,
                                     'data': base64.b64encode(path.read_bytes()).decode('ascii')}})
    config = {'responseMimeType': 'application/json'}
    if schema is not None:
        config['responseJsonSchema'] = compatible_schema(schema)
    return {'contents': [{'role': 'user', 'parts': parts}], 'generationConfig': config}


def error_detail(response, secret=''):
    try:
        error = response.json().get('error', {})
    except (ValueError, AttributeError):
        error = {}
    if not isinstance(error, dict):
        error = {}
    message = str(error.get('message', '')).replace('\n', ' ').strip()
    if secret:
        message = message.replace(secret, '[KEY]')
    message = re.sub(r'AIza[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]+', '[KEY]', message)
    status = str(error.get('status', '')).strip()
    return ' | '.join(part for part in (status, f'code {response.status_code}', message[:700]) if part)


def generate(prompt, images, model, schema, api_key, check):
    if not api_key:
        raise ValueError('Chưa có Gemini API key. Nhập key trong Kết nối rồi bấm Áp dụng key.')
    model = normalize_model(model)
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
    def post(body):
        for attempt in range(3):
            check()
            try:
                result = requests.post(url, json=body, headers={'x-goog-api-key': api_key}, timeout=(15, 300))
            except requests.RequestException:
                raise RuntimeError('Không kết nối được Gemini hoặc request hết thời gian. Kiểm tra mạng rồi thử lại.') from None
            check()
            if result.status_code not in (429, 500, 502, 503, 504) or attempt == 2:
                return result
            for _ in range(2 ** attempt * 5):
                check()
                time.sleep(1)

    response = post(request_body(prompt, images, schema))
    schema_mode = 'structured'
    if response.status_code == 400:
        # Some Gemini models reject valid but relatively complex response
        # schemas. JSON mode is a safe fallback because the same Pydantic model
        # is validated by providers.ask_ai before anything is cached or saved.
        compact = compatible_schema(schema)
        fallback_prompt = (prompt + '\nReturn exactly one JSON object with no markdown. '
                           'It must match this JSON schema; include every required field:\n' +
                           json.dumps(compact, ensure_ascii=False, separators=(',', ':')))
        response = post(request_body(fallback_prompt, images))
        schema_mode = 'json'
    if not response.ok:
        hints = {400: 'Yêu cầu Gemini không hợp lệ. Kiểm tra model, JSON schema hoặc dữ liệu.',
                 401: 'API key Gemini không hợp lệ.',
                 402: ('Gemini AI Studio đang hết prepaid credit. Hãy mở Billing trong AI Studio và chọn Buy credits '
                       '(không chỉ Make a payment ở Google Cloud Postpay), hoặc áp dụng key thuộc project còn hạn mức; '
                       'dữ liệu phân tích đã hoàn tất vẫn được giữ trong cache.'),
                 403: 'API key không có quyền truy cập hoặc khu vực không được hỗ trợ.',
                 404: 'Không tìm thấy model trong tài khoản này.',
                 429: 'Đã chạm quota hoặc giới hạn tốc độ Gemini. Kiểm tra quota/billing hoặc chờ giới hạn được đặt lại.'}
        raise RuntimeError(f"Gemini {response.status_code}: {hints.get(response.status_code, 'Dịch vụ Gemini đang lỗi; thử lại sau.')}\nChi tiết Google: {error_detail(response, api_key)}")
    try:
        data = response.json()
    except ValueError:
        raise RuntimeError('Gemini trả phản hồi không phải JSON hợp lệ.') from None
    if data.get('promptFeedback', {}).get('blockReason'):
        raise RuntimeError('Gemini chặn nội dung đầu vào; chưa lưu kết quả.')
    candidates = data.get('candidates') or []
    candidate = candidates[0] if candidates else {}
    if candidate.get('finishReason') != 'STOP':
        reason = candidate.get('finishReason', 'EMPTY')
        hint = 'Đầu ra bị cắt do giới hạn token.' if reason == 'MAX_TOKENS' else 'Không có kết quả hoàn chỉnh hoặc bị chặn.'
        raise RuntimeError('Gemini: ' + hint + ' Chưa lưu kết quả; thử lại hoặc đổi model.')
    text = ''.join(part.get('text', '') for part in candidate.get('content', {}).get('parts', []) if not part.get('thought'))
    if not text.strip():
        raise RuntimeError('Gemini không trả nội dung JSON. Hãy thử lại hoặc đổi model.')
    usage = dict(data.get('usageMetadata', {}))
    usage['_air3view_schema_mode'] = schema_mode
    return text, usage


def list_models(api_key):
    if not api_key:
        raise ValueError('Chưa có Gemini API key. Nhập key rồi bấm Áp dụng key để tải model.')
    models, seen_tokens, token = {}, set(), None
    for _ in range(50):
        params = {'pageSize': 1000}
        if token:
            params['pageToken'] = token
        try:
            response = requests.get('https://generativelanguage.googleapis.com/v1beta/models',
                                    headers={'x-goog-api-key': api_key}, params=params, timeout=(10, 30))
        except requests.RequestException:
            raise RuntimeError('Không tải được danh sách model Gemini. Kiểm tra mạng rồi thử lại.') from None
        if not response.ok:
            raise RuntimeError(f'Gemini {response.status_code}: {error_detail(response, api_key)}')
        try:
            data = response.json()
            items = data.get('models', [])
            if not isinstance(items, list):
                raise ValueError()
            for item in items:
                if not isinstance(item, dict) or 'generateContent' not in (item.get('supportedGenerationMethods') or []):
                    continue
                name = item.get('name', '')
                if not isinstance(name, str) or not name.startswith('models/'):
                    continue
                identifier = name.removeprefix('models/')
                if not MODEL_PATTERN.fullmatch(identifier):
                    continue
                models[identifier] = {'id': identifier,
                                      'name': item.get('displayName') or identifier,
                                      'description': item.get('description') or ''}
            token = data.get('nextPageToken')
            if token and not isinstance(token, str):
                raise ValueError()
        except (ValueError, TypeError):
            raise RuntimeError('Gemini trả danh sách model không hợp lệ.') from None
        if not token:
            return sorted(models.values(), key=lambda item: item['id'])
        if token in seen_tokens:
            break
        seen_tokens.add(token)
    raise RuntimeError('Danh sách model Gemini phân trang không hoàn tất; thử tải lại.')


def check_model(model, api_key):
    if not api_key:
        raise ValueError('Chưa có Gemini API key. Nhập key rồi bấm Áp dụng key trước khi kiểm tra.')
    model = normalize_model(model)
    from .models import StoryAnswer
    try:
        text, usage = generate(
            ('Return one valid JSON object matching the schema. Use exactly three selections in order: '
             'opening, development, ending. Use part 1, chronological positive start/end values, priority '
             'between 0 and 1, narration_offset 0, and non-empty reason and evidence strings.'),
            [], model, StoryAnswer.model_json_schema(), api_key, lambda: None)
        StoryAnswer.model_validate_json(text)
    except (ValueError, TypeError, json.JSONDecodeError):
        raise RuntimeError('Gemini không trả đúng JSON theo schema lập highlight. Hãy chọn model khác.') from None
    return {'model': model, 'structured_output': usage.get('_air3view_schema_mode') == 'structured',
            'story_schema': True, 'schema_mode': usage.get('_air3view_schema_mode', 'structured')}
