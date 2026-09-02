from plugins.base_plugin.base_plugin import BasePlugin
from plugins.superprod.sp import Sp, State
import logging

logger = logging.getLogger(__name__)

FONT_SIZES = {
    "x-small": 0.7,
    "small": 0.9,
    "normal": 1,
    "large": 1.1,
    "x-large": 1.3
}

class Superprod(BasePlugin):
    def __init__(self, config, **dependencies):
        super().__init__(config, **dependencies)
        self.sp = Sp()

    def generate_image(self, settings, device_config):
        logger.info("=== Superprod Plugin: Starting image generation ===")
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        today_taks = self.sp.get_tasks(["task", "list", "--today"], "Today's tasks")
        due_taks = self.sp.get_tasks(["task", "list", "--past-due"], "Past-due tasks")
        lists = [today_taks, due_taks]

        template_params = {
            "title": settings.get('title'),
            "list_style": settings.get('listStyle', 'disc'),
            "font_scale": FONT_SIZES.get(settings.get('fontSize', 'normal'), 1),
            "lists": lists,
            "plugin_settings": settings
        }

        image = self.render_image(dimensions, "superprod.html", "superprod.css", template_params)
        return image

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = True
        return template_params
