"""Actionable provider failures without echoing CLI prompts into the UI."""
import json
import re


class AIProviderError(RuntimeError):
    def __init__(self, provider, code, message, retryable=False):
        self.provider, self.code, self.retryable = provider, code, retryable
        super().__init__(f'[AI:{code}] {provider}: {message}')


def codex_failure(details):
    # CLI logs contain prompts and multiple JSON error envelopes. Extract only
    # actual error envelopes; never display the log tail as a login hint.
    errors = []
    decoder = json.JSONDecoder()
    for match in re.finditer(r'(?:ERROR:\s*|^\s*)(\{)', details, re.M):
        try:
            value, _ = decoder.raw_decode(details[match.start(1):])
            error = value.get('error', value)
            if isinstance(error, dict) and (error.get('code') or error.get('type')):
                errors.append(error)
        except (ValueError, AttributeError):
            pass
    codes = {str(e.get('code') or '') for e in errors}
    text = '\n'.join(str(e.get('message') or '') for e in errors) if errors else details
    lower = text.lower()
    if 'invalid_json_schema' in codes or 'invalid_json_schema' in details:
        missing = re.search(r"Missing '([A-Za-z_][A-Za-z0-9_]*)'", text)
        reason = ' Thiếu trường bắt buộc ' + missing.group(1) + '.' if missing else ''
        return AIProviderError('Codex', 'invalid_json_schema',
                               'Schema kết quả không hợp lệ.' + reason +
                               ' Cần cập nhật ứng dụng; thử lại hoặc đăng nhập lại không sửa được schema.')
    if codes.intersection({'usage_limit_reached', 'insufficient_quota', 'billing_hard_limit_reached'}) or 'hit your usage limit' in lower:
        return AIProviderError('Codex', 'quota',
                               'Đã hết hạn mức. Chờ hạn mức được đặt lại hoặc chọn OpenAI API trong Kết nối, '
                               'rồi Thử lại. Các đoạn phân tích đã hoàn tất vẫn có trong cache.')
    if codes.intersection({'invalid_api_key', 'authentication_error', 'token_expired'}) or any(
            marker in lower for marker in ('unauthorized', '401', 'not logged in', 'authentication failed')):
        return AIProviderError('Codex', 'authentication', 'Phiên xác thực không hợp lệ; kiểm tra đăng nhập Codex.')
    if 'rate_limit_exceeded' in codes or 'rate limit' in lower:
        return AIProviderError('Codex', 'rate_limit', 'Rate limit; chờ rồi thử lại từ tiến độ đã lưu.', True)
    if any(marker in lower for marker in ('timed out', 'timeout')):
        return AIProviderError('Codex', 'timeout', 'Timeout; thử lại từ tiến độ đã lưu.', True)
    if any(marker in lower for marker in ('connection reset', 'connection aborted', 'temporarily unavailable',
                                         'failed to send request', '502', '503', '504')):
        return AIProviderError('Codex', 'transport', 'Kết nối tạm thời lỗi; thử lại từ tiến độ đã lưu.', True)
    return AIProviderError('Codex', 'unknown',
                           'CLI không trả được kết quả. Xem log chẩn đoán trong analysis-cache; '
                           'chưa đủ bằng chứng để kết luận lỗi đăng nhập.')
