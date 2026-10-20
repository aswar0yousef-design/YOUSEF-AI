"""Controlling this PC: a short list of safe actions, each one approved by the user."""

from .control import DEFAULT_APPS, Device, DeviceError, check_app, check_url

__all__ = ["DEFAULT_APPS", "Device", "DeviceError", "check_app", "check_url"]
