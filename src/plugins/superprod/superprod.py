from plugins.base_plugin.base_plugin import BasePlugin
from plugins.superprod.sp import Sp, State
import logging

logger = logging.getLogger(__name__)


class Superprod(BasePlugin):
    def __init__(self, config, **dependencies):
        super().__init__(config, **dependencies)
        self.sp = Sp()

    def generate_image(self, settings, device_config):
        logger.info("=== Superprod Plugin: Starting image generation ===")
        task_number = settings["task_number"]
        # self.render_image()

    def generate_settings_template(self):
        template_params = super().generate_settings_template()

        return template_params
