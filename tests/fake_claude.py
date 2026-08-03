"""A minimal stand-in for the anthropic client, for testing quiz_engine
without making real API calls."""


class FakeTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class FakeMessage:
    def __init__(self, text: str):
        self.content = [FakeTextBlock(text)]


class FakeMessages:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls = 0
        self.last_kwargs: dict = {}

    def create(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        text = self._responses[min(self.calls, len(self._responses)) - 1]
        return FakeMessage(text)


class FakeAnthropicClient:
    def __init__(self, responses: list[str]):
        self.messages = FakeMessages(responses)
