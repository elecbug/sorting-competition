"""Student identifiers as displayed outside administrator views."""


def mask_student_id(value):
    value = str(value)
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


def student_identity(record, *, admin=False):
    result = dict(record)
    if not admin:
        result["student_id"] = mask_student_id(result["student_id"])
        if "nickname" in result:
            result["nickname"] = result["student_id"]
    return result
