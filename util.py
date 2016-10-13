import config

def _get_config(name, default=None):
    return config.__dict__.get(name) or default

def _set_default(name, value):
    config.__dict__[name] = _get_config(name, value)

config.get = _get_config
config.set_default = _set_default
