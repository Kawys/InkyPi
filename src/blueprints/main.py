from flask import Blueprint, request, jsonify, current_app, render_template, send_file
import os
import json
from datetime import datetime
from plugins.plugin_registry import get_plugin_instance
from plugins.superprod.sp import State
from utils.app_utils import rgetattr
import logging
from ast import literal_eval

logger = logging.getLogger(__name__)
main_bp = Blueprint("main", __name__)


class MethodReturnEncoder(json.JSONEncoder):
    """Serialize non-default types (e.g. superprod's State enum) into JSON-safe values."""

    def default(self, obj):
        if isinstance(obj, State):
            return obj.name
        return super().default(obj)

@main_bp.route('/')
def main_page():
    device_config = current_app.config['DEVICE_CONFIG']
    return render_template('inky.html', config=device_config.get_config(), plugins=device_config.get_plugins())

@main_bp.route('/api/current_image')
def get_current_image():
    """Serve current_image.png with conditional request support (If-Modified-Since)."""
    image_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'images', 'current_image.png')

    if not os.path.exists(image_path):
        return jsonify({"error": "Image not found"}), 404

    # Get the file's last modified time (truncate to seconds to match HTTP header precision)
    file_mtime = int(os.path.getmtime(image_path))
    last_modified = datetime.fromtimestamp(file_mtime)

    # Check If-Modified-Since header
    if_modified_since = request.headers.get('If-Modified-Since')
    if if_modified_since:
        try:
            # Parse the If-Modified-Since header
            client_mtime = datetime.strptime(if_modified_since, '%a, %d %b %Y %H:%M:%S %Z')
            client_mtime_seconds = int(client_mtime.timestamp())

            # Compare (both now in seconds, no sub-second precision)
            if file_mtime <= client_mtime_seconds:
                return '', 304
        except (ValueError, AttributeError):
            pass

    # Send the file with Last-Modified header
    response = send_file(image_path, mimetype='image/png')
    response.headers['Last-Modified'] = last_modified.strftime('%a, %d %b %Y %H:%M:%S GMT')
    response.headers['Cache-Control'] = 'no-cache'
    return response


@main_bp.route('/api/plugin_order', methods=['POST'])
def save_plugin_order():
    """Save the custom plugin order."""
    device_config = current_app.config['DEVICE_CONFIG']

    data = request.get_json() or {}
    order = data.get('order', [])

    if not isinstance(order, list):
        return jsonify({"error": "Order must be a list"}), 400

    device_config.set_plugin_order(order)

    return jsonify({"success": True})

@main_bp.route('/api/execute', methods=['POST'])
def execute_command():
    device_config = current_app.config['DEVICE_CONFIG']

    data = request.get_json() or {}
    plugin_id = data.get('plugin_id')
    method_name = data.get('method')
    args = data.get('args')

    if not plugin_id:
       return {'error': 'You must supply plugin id'}, 400
    if not method_name:
        return {'error': 'You must supply method name'}, 400

    plugin_config = device_config.get_plugin(plugin_id)
    if not plugin_config:
        return {'error': 'Plugin not found'}, 404

    try:
        plugin_instance = get_plugin_instance(plugin_config)
        method_object = rgetattr(plugin_instance, method_name)
        if method_object:
            if args:
                args = literal_eval(args)
            else:
                args = {}
            result = method_object(**args)
            return json.dumps({"success": True, "result": result}, cls=MethodReturnEncoder), 200
        else:
            logger.error(f"Command not found: {plugin_instance}.{method_name}()")
            return {"error": f"Command not found."}, 500
    except Exception as e:
        logger.exception("EXCEPTION CAUGHT: " + str(e))
        return {"error": f"An error occurred: {str(e)}"}, 500
