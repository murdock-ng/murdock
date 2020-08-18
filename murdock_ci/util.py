import os
import sys

from .config import Config

class MurdockConfig(Config):
    def set_defaults(s):
        super().set_defaults()
        s.config["fail_labels"] = set(s.config.get("fail_labels", []))
        s.set_default("ci_ready_label", "Ready for CI build")
        s.set_default("context", "Murdock")
        s.set_default("port", 3000)
        s.set_default("scripts_dir", os.getcwd() + "/scripts")
        s.set_default("set_status", False)
        s.set_default("sigterm_timeout", 100)
        s.set_default("github_username", None)
        s.set_default("github_password", None)
        s.set_default("github_apikey", None)

if len(sys.argv) > 1:
    config_file = sys.argv[1]
else:
    config_file = "/etc/murdock.toml"

config = MurdockConfig(config_file)
