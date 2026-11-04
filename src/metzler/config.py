"""Configuration helpers: the minimal .env loader and INDOOR_* lookups."""

from pathlib import Path


def load_env(path):
    """minimal .env loader: KEY=VALUE lines, # comments, optional quotes"""
    vals = {}
    try:
        with Path(path).open() as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return vals


def env_default(env, key, default=None, cast=None):
    v = env.get("INDOOR_" + key.upper(), default)
    if v == "":
        v = default
    if cast and v is not None:
        return cast(v)
    return v


def env_bool(env, key, default=False):
    """INDOOR_<KEY> as a flag; unset -> `default`"""
    v = str(env.get("INDOOR_" + key.upper(), "")).lower()
    return default if v == "" else v in ("1", "true", "yes", "on")


def answer_policy(value):
    """INDOOR_AUTO_ANSWER: off | all | extension (only calls the main indoor places to us)"""
    v = str(value or "").strip().lower()
    if v in ("", "0", "false", "no", "off"):
        return "off"
    return "extension" if v == "extension" else "all"
