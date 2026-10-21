import re


def redact(value):
    text = str(value)
    text = re.sub(r'https?://[^\s"<>]+', '[传输地址已隐藏]', text)
    text = re.sub(r'(?i)(token|cookie|authorization|password|api_key)\s*[=:]\s*[^\s,;]+',
                  r'\1=[已隐藏]', text)
    return text


class FlowError(Exception):
    def __init__(self, code, message, *, retryable=False, uncertain=False):
        super().__init__(redact(message))
        self.code, self.retryable, self.uncertain = code, retryable, uncertain

    def as_dict(self):
        return {"code": self.code, "message": str(self), "retryable": self.retryable,
                "execution_uncertain": self.uncertain}
