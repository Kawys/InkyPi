import os
import logging
import psutil
import pytz
from datetime import datetime
from plugins.plugin_registry import get_plugin_instance
from utils.image_utils import compute_image_hash
from model import RefreshInfo
from PIL import Image

logger = logging.getLogger(__name__)

class Refresh:
    def __init__(self, device_config, display_manager):
        self.device_config = device_config
        self.display_manager = display_manager
        self.last_image_hash = None


    def manual_refresh(self, plugin_id: str, plugin_settings: dict):
        current_dt = self._get_current_datetime()

        plugin_config = self.device_config.get_plugin(plugin_id)
        if plugin_config is None:
            logger.error(f"Plugin config not found for '{plugin_id}'.")
            return
        plugin = get_plugin_instance(plugin_config)
        image = plugin.generate_image(plugin_settings, self.device_config)
        image_hash = compute_image_hash(image)

        refresh_info = {
            "refresh_type": "Manual Update",
            "plugin_id": plugin_id,
            "refresh_time": current_dt.isoformat(),
            "image_hash": image_hash
        }

        # check if image is the same as current image
        if image_hash != self.last_image_hash:
            self.last_image_hash = image_hash
            logger.info(f"!!Updating display. | refresh_info: {refresh_info}")
            self.display_manager.display_image(image, image_settings=plugin.config.get("image_settings", []))
        else:
            logger.info(f"!!Image already displayed, skipping refresh. | refresh_info: {refresh_info}")

        # update latest refresh data in the device config
        self.device_config.refresh_info = RefreshInfo(**refresh_info)

    def playlist_refresh(self, playlist=None, plugin_instance=None, force=False):
        """Generate new image for plugins in Playlist context."""
        current_dt = self._get_current_datetime()
        if playlist is None or plugin_instance is None:
            playlist_manager = self.device_config.get_playlist_manager()

            playlist = playlist_manager.determine_active_playlist(current_dt)
            if playlist is None:
                logger.error(f"!!There is no active playlist.")
                return None

            plugin_instance = playlist.get_next_plugin()
            if plugin_instance is None:
                logger.error(f"!!Playlist is empty.")
                return None

        plugin_config = self.device_config.get_plugin(plugin_instance.plugin_id)
        if plugin_config is None:
            logger.error(f"!!Plugin config not found for '{plugin_instance.plugin_id}'.")
            return None
        plugin = get_plugin_instance(plugin_config)

        plugin_image_path = os.path.join(self.device_config.plugin_image_dir, plugin_instance.get_image_path())

        # Check if a refresh is needed based on the plugin instance's criteria
        if plugin_instance.should_refresh(current_dt) or force:
            logger.info(f"!!Refreshing plugin instance. | plugin_instance: '{plugin_instance.name}'")
            # Generate a new image
            image = plugin.generate_image(plugin_instance.settings, self.device_config)
            image.save(plugin_image_path)
            plugin_instance.latest_refresh_time = current_dt.isoformat()
        else:
            logger.info(f"!!Not time to refresh plugin instance, using latest image. | plugin_instance: {plugin_instance.name}.")
            # Load the existing image from disk
            with Image.open(plugin_image_path) as img:
                image = img.copy()

        image_hash = compute_image_hash(image)

        refresh_info = {
            "refresh_type": "Playlist",
            "playlist": playlist.name,
            "plugin_id": plugin_instance.plugin_id,
            "plugin_instance": plugin_instance.name,
            "refresh_time": current_dt.isoformat(),
            "image_hash": image_hash
        }
        # check if image is the same as current image
        if image_hash != self.last_image_hash:
            self.last_image_hash = image_hash
            logger.info(f"!!Updating display. | refresh_info: {refresh_info}")
            self.display_manager.display_image(image, image_settings=plugin.config.get("image_settings", []))
        else:
            logger.info(f"!!Image already displayed, skipping refresh. | refresh_info: {refresh_info}")

        # update latest refresh data in the device config
        self.device_config.refresh_info = RefreshInfo(**refresh_info)

        return image


    def _get_current_datetime(self):
        """Retrieves the current datetime based on the device's configured timezone."""
        tz_str = self.device_config.get_config("timezone", default="UTC")
        return datetime.now(pytz.timezone(tz_str))

    def log_system_stats(self):
        metrics = {
            'cpu_percent': psutil.cpu_percent(interval=1),
            'memory_percent': psutil.virtual_memory().percent,
            'disk_percent': psutil.disk_usage('/').percent,
            'load_avg_1_5_15': os.getloadavg(),
            'swap_percent': psutil.swap_memory().percent,
            'net_io': {
                'bytes_sent': psutil.net_io_counters().bytes_sent,
                'bytes_recv': psutil.net_io_counters().bytes_recv
            }
        }

        logger.info(f"System Stats: {metrics}")
