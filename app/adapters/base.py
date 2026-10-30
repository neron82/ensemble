from abc import ABC, abstractmethod

class LLMAdapter(ABC):
    @abstractmethod
    async def send_message(self, participant, conversation_context, directed_user_message, options):
        pass

    @abstractmethod
    async def validate_config(self, participant):
        pass

    @abstractmethod
    async def test_connection(self, participant):
        pass
