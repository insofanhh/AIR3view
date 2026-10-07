"""Strict AI transport schemas, independent of defaults in persisted models."""
import copy


class AISchemaError(RuntimeError):
    code = 'invalid_json_schema'
    retryable = False

    def __init__(self, model, path, reason):
        self.response_model, self.path = model, path
        super().__init__(f'[AI:invalid_json_schema] Schema {model} không hợp lệ tại {path}: {reason}. '
                         'Cần cập nhật ứng dụng; thử lại cùng cấu hình không sửa được lỗi schema.')


def model_schema(model):
    try:
        return model.model_json_schema()
    except (ValueError, TypeError, KeyError) as error:
        raise AISchemaError(model.__name__, '#',
                            'không tạo được schema/reference (' + type(error).__name__ + ')') from error


def strict_schema(model):
    """Never mutate Pydantic's schema or relax unsupported object contracts."""
    schema = copy.deepcopy(model_schema(model))
    name = model.__name__
    unsupported = {'allOf', 'not', 'dependentRequired', 'dependentSchemas', 'if', 'then', 'else',
                   'patternProperties', 'oneOf', 'prefixItems'}
    references = []

    def visit(node, path):
        if not isinstance(node, dict) or not node:
            raise AISchemaError(name, path, 'thiếu kiểu dữ liệu cụ thể')
        rejected = unsupported.intersection(node)
        if rejected:
            raise AISchemaError(name, path, 'cấu trúc chưa hỗ trợ: ' + ', '.join(sorted(rejected)))
        node.pop('default', None)
        if 'const' in node:
            node['enum'] = [node.pop('const')]
        if '$ref' in node:
            references.append((node['$ref'], path))
        if node.get('type') == 'object':
            properties = node.get('properties')
            if not isinstance(properties, dict) or node.get('additionalProperties') not in (None, False):
                raise AISchemaError(name, path, 'object phải có tập thuộc tính cố định')
            node['required'] = list(properties)
            node['additionalProperties'] = False
        for keyword in ('properties', '$defs'):
            for key, value in node.get(keyword, {}).items():
                visit(value, path + '/' + keyword + '/' + key)
        if 'items' in node:
            visit(node['items'], path + '/items')
        for index, value in enumerate(node.get('anyOf', [])):
            visit(value, path + '/anyOf/' + str(index))
        if not any(key in node for key in ('type', '$ref', 'anyOf')):
            raise AISchemaError(name, path, 'thiếu type, $ref hoặc anyOf')

    visit(schema, '#')
    if schema.get('type') != 'object' or 'anyOf' in schema:
        raise AISchemaError(name, '#', 'root phải là object')
    for ref, path in references:
        if not isinstance(ref, str) or not ref.startswith('#/'):
            raise AISchemaError(name, path, 'chỉ hỗ trợ reference nội bộ')
        target = schema
        try:
            for token in ref[2:].split('/'):
                target = target[token.replace('~1', '/').replace('~0', '~')]
        except (KeyError, TypeError):
            raise AISchemaError(name, path, 'reference không tồn tại: ' + ref) from None
    return schema


def response_models():
    """Registry of transport models; imports stay lazy to avoid provider cycles."""
    from .models import AnalysisAnswer, TranslationAnswer, StoryAnswer
    from .efficient_analysis import EvidenceAnswer
    from .plan_first import FootagePlan
    from .reaction_cops import OptimizedBatch, ReactionFootagePlan, ReactionTitle
    from .reaction_dubbing import DubAnswer, DubReview
    from .reaction_review import ReviewOutline, ReviewText, ReviewCheck
    from .scene_repair import SceneRepairs
    from .source_speech import SpeechRoles
    from .story_bridges import CoverageReview
    from .story_schedule import ScheduledNarration
    from .voice_repair import NarrationReply
    return (AnalysisAnswer, TranslationAnswer, StoryAnswer, EvidenceAnswer, FootagePlan,
            OptimizedBatch, ReactionFootagePlan, ReactionTitle, DubAnswer, DubReview,
            ReviewOutline, ReviewText, ReviewCheck, SceneRepairs, SpeechRoles,
            CoverageReview, ScheduledNarration, NarrationReply)


def preflight(provider, check=lambda: None):
    if provider not in ('codex', 'openai'):
        return
    for model in response_models():
        check()
        strict_schema(model)
