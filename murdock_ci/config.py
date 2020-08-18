import pytoml as toml

from .log import log

class Config(object):
    def __init__(s, filename):
        s.config = {}
        s.load(filename)
        s.set_defaults()

    def set_default(s, key, val):
        global _config
        s.config[key] = s.config.get(key, val)

    def load(s, config_file):
        log.info("loading configuration file %s", config_file)
        try:
            with open(config_file, "r") as f:
                s.config = toml.loads(f.read())
        except FileNotFoundError as e:
            log.error("configuration file %s not found!" % config_file)
            raise e

    def set_defaults(s):
        pass

    def __getattr__(s, key):
        return s.config[key]

    def __str__(s):
        return str(s.config)
