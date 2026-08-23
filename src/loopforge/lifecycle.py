from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigStore, Settings, default_data_dir
from .logging_setup import close_logging, configure_logging
from .media_tools import DiscoveryError, MediaTools, discover_media_tools

Discoverer = Callable[[Settings, Path | None], MediaTools]


@dataclass(slots=True)
class Runtime:
    settings: Settings
    logger: logging.Logger
    media_tools: MediaTools | None


class Lifecycle:
    def __init__(
        self,
        config: ConfigStore | None = None,
        data_dir: Path | None = None,
        bundled_dir: Path | None = None,
        discoverer: Discoverer = discover_media_tools,
    ) -> None:
        self.data_dir = data_dir or default_data_dir()
        self.config = config or ConfigStore(self.data_dir / "settings.json")
        self.bundled_dir = bundled_dir
        self.discoverer = discoverer
        self.runtime: Runtime | None = None

    def startup(self) -> Runtime:
        settings = self.config.load()
        logger = configure_logging(self.data_dir / "logs")
        try:
            tools = self.discoverer(settings, self.bundled_dir)
            logger.info("Media tools ready: %s; %s", tools.ffmpeg_version, tools.ffprobe_version)
        except DiscoveryError as error:
            tools = None
            logger.warning("%s", error)
        self.runtime = Runtime(settings, logger, tools)
        logger.info("Application started")
        return self.runtime

    def shutdown(self) -> None:
        if self.runtime is None:
            return
        self.config.save(self.runtime.settings)
        self.runtime.logger.info("Application stopped")
        close_logging(self.runtime.logger)
        self.runtime = None
