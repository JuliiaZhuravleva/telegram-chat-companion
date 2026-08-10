"""
Dishka dependency injection providers.

Scopes:
- APP:     Lives for the entire bot process (pool, settings, AI router)
- REQUEST: Created per Telegram update (repositories, services)
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import asyncpg
from dishka import Provider, Scope, from_context, provide

from src.config import Settings
from src.database.connection import close_pool, create_pool
from src.database.repositories.abuse import AbuseRepository
from src.database.repositories.activity import ActivityRepository
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.database.repositories.knowledge import KnowledgeRepository
from src.database.repositories.memory import MemoryRepository
from src.database.repositories.messages import MessageRepository
from src.database.repositories.observability import ObservabilityRepository
from src.database.repositories.reactions import ReactionRepository
from src.database.repositories.response_log import ResponseLogRepository
from src.database.repositories.rules import RulesRepository
from src.database.repositories.stickers import StickerRepository
from src.services.abuse.checker import AntiAbuseChecker
from src.services.abuse.filter import AbuseFilter
from src.services.abuse.notifications import AbuseNotificationService
from src.services.ai.router import AIRouter
from src.services.chat_config import ChatConfigService
from src.services.costs.spend_limit import SpendLimitService
from src.services.modules.image import ImageAnalysisService
from src.services.modules.links import LinkExtractorService
from src.services.modules.sticker import StickerLearningService, StickerResponderService
from src.services.modules.summary import SummaryService
from src.services.modules.voice import VoiceTranscriptionService
from src.services.rag.memory import RAGMemoryService
from src.services.relevancy.gate import RelevancyGate
from src.services.rules import RuleActionExecutor, RuleEngine
from src.services.text.pipeline import TextProcessingPipeline


class AppProvider(Provider):
    """Infrastructure that lives for the entire application."""

    scope = Scope.APP

    settings = from_context(provides=Settings, scope=Scope.APP)

    @provide
    async def get_pool(self, settings: Settings) -> AsyncIterator[asyncpg.Pool]:
        pool = await create_pool(settings.database_url)
        yield pool
        await close_pool(pool)

    @provide
    async def get_ai_router(
        self, settings: Settings, pool: asyncpg.Pool
    ) -> AsyncIterator[AIRouter]:
        """Yield rather than return, so Dishka registers a teardown.

        ``AIRouter.close()`` exists and, as a plain ``return`` provider, was
        never reached: ``container.close()`` on shutdown had nothing to call and
        the class has no ``__del__``, so the ``httpx.AsyncClient`` each provider
        opens lazily was never closed. ``get_pool`` above already had the right
        shape; this one simply did not follow it.
        """
        router = AIRouter(settings, response_log_repo=ResponseLogRepository(pool))
        yield router
        await router.close()


class RepositoryProvider(Provider):
    """Repositories — one instance per request."""

    scope = Scope.REQUEST

    @provide
    def bot_config_repo(self, pool: asyncpg.Pool) -> BotConfigRepository:
        return BotConfigRepository(pool)

    @provide
    def chat_settings_repo(self, pool: asyncpg.Pool) -> ChatSettingsRepository:
        return ChatSettingsRepository(pool)

    @provide
    def message_repo(self, pool: asyncpg.Pool) -> MessageRepository:
        return MessageRepository(pool)

    @provide
    def memory_repo(self, pool: asyncpg.Pool) -> MemoryRepository:
        return MemoryRepository(pool)

    @provide
    def abuse_repo(self, pool: asyncpg.Pool) -> AbuseRepository:
        return AbuseRepository(pool)

    @provide
    def activity_repo(self, pool: asyncpg.Pool) -> ActivityRepository:
        return ActivityRepository(pool)

    @provide
    def response_log_repo(self, pool: asyncpg.Pool) -> ResponseLogRepository:
        return ResponseLogRepository(pool)

    @provide
    def sticker_repo(self, pool: asyncpg.Pool) -> StickerRepository:
        return StickerRepository(pool)

    @provide
    def rules_repo(self, pool: asyncpg.Pool) -> RulesRepository:
        return RulesRepository(pool)

    @provide
    def admin_repo(self, pool: asyncpg.Pool) -> AdminRepository:
        return AdminRepository(pool)

    @provide
    def knowledge_repo(self, pool: asyncpg.Pool) -> KnowledgeRepository:
        return KnowledgeRepository(pool)

    @provide
    def reaction_repo(self, pool: asyncpg.Pool) -> ReactionRepository:
        return ReactionRepository(pool)

    @provide
    def observability_repo(self, pool: asyncpg.Pool) -> ObservabilityRepository:
        return ObservabilityRepository(pool)


class ServiceProvider(Provider):
    """Application services — one instance per request."""

    scope = Scope.REQUEST

    @provide
    def chat_config_service(
        self,
        settings: Settings,
        bot_config_repo: BotConfigRepository,
        chat_settings_repo: ChatSettingsRepository,
    ) -> ChatConfigService:
        return ChatConfigService(
            yaml_settings=settings.bot,
            bot_config_repo=bot_config_repo,
            chat_settings_repo=chat_settings_repo,
        )

    @provide
    def rag_memory_service(
        self,
        settings: Settings,
        memory_repo: MemoryRepository,
        ai_router: AIRouter,
    ) -> RAGMemoryService:
        return RAGMemoryService(
            memory_repo=memory_repo,
            ai_router=ai_router,
            min_similarity=settings.rag.min_similarity,
            max_results=settings.rag.max_results,
        )

    @provide
    def anti_abuse_checker(self, abuse_repo: AbuseRepository) -> AntiAbuseChecker:
        return AntiAbuseChecker(abuse_repo)

    @provide
    def abuse_filter(
        self,
        abuse_repo: AbuseRepository,
        ai_router: AIRouter,
    ) -> AbuseFilter:
        return AbuseFilter(abuse_repo, ai_router)

    @provide
    def abuse_notification_service(
        self,
        bot_config_repo: BotConfigRepository,
    ) -> AbuseNotificationService:
        return AbuseNotificationService(bot_config_repo)

    @provide
    def summary_service(
        self,
        message_repo: MessageRepository,
        ai_router: AIRouter,
    ) -> SummaryService:
        return SummaryService(message_repo, ai_router)

    @provide
    def link_extractor_service(self, settings: Settings) -> LinkExtractorService:
        return LinkExtractorService(youtube_api_key=settings.youtube_api_key)

    @provide
    def text_pipeline(
        self,
        ai_router: AIRouter,
        abuse_checker: AntiAbuseChecker,
        message_repo: MessageRepository,
        response_log_repo: ResponseLogRepository,
        rag_service: RAGMemoryService,
        link_service: LinkExtractorService,
        sticker_service: StickerResponderService,
        knowledge_repo: KnowledgeRepository,
        observability_repo: ObservabilityRepository,
    ) -> TextProcessingPipeline:
        return TextProcessingPipeline(
            ai_router=ai_router,
            abuse_checker=abuse_checker,
            message_repo=message_repo,
            response_log_repo=response_log_repo,
            rag_service=rag_service,
            link_service=link_service,
            sticker_service=sticker_service,
            knowledge_repo=knowledge_repo,
            observability_repo=observability_repo,
        )

    @provide
    def voice_transcription_service(
        self,
        ai_router: AIRouter,
        message_repo: MessageRepository,
    ) -> VoiceTranscriptionService:
        return VoiceTranscriptionService(ai_router, message_repo)

    @provide
    def image_analysis_service(self, ai_router: AIRouter) -> ImageAnalysisService:
        return ImageAnalysisService(ai_router)

    @provide
    def sticker_learning_service(
        self,
        ai_router: AIRouter,
        sticker_repo: StickerRepository,
        settings: Settings,
    ) -> StickerLearningService:
        module = settings.modules.get("sticker_intelligence")
        debug_mode = getattr(module, "debug_mode", False) if module else False
        return StickerLearningService(ai_router, sticker_repo, debug_mode=debug_mode)

    @provide
    def sticker_responder_service(
        self,
        sticker_service: StickerLearningService,
        sticker_repo: StickerRepository,
    ) -> StickerResponderService:
        return StickerResponderService(sticker_service, sticker_repo)

    @provide
    def relevancy_gate(
        self,
        ai_router: AIRouter,
        message_repo: MessageRepository,
        response_log_repo: ResponseLogRepository,
        observability_repo: ObservabilityRepository,
    ) -> RelevancyGate:
        return RelevancyGate(ai_router, message_repo, response_log_repo, observability_repo)

    @provide
    def spend_limit_service(
        self,
        response_log_repo: ResponseLogRepository,
        bot_config_repo: BotConfigRepository,
    ) -> SpendLimitService:
        return SpendLimitService(response_log_repo, bot_config_repo)

    @provide
    def rule_engine(self, rules_repo: RulesRepository) -> RuleEngine:
        return RuleEngine(rules_repo)

    @provide
    def rule_action_executor(self, bot_config_repo: BotConfigRepository) -> RuleActionExecutor:
        return RuleActionExecutor(bot_config_repo)
