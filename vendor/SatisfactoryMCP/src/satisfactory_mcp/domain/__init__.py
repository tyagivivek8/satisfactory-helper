"""The game's own subjects: a world, its progression, power, factories and plans.

One package per domain idea. Everything here returns dataclasses and dicts and
never formatted text -- the presenters do that -- and nothing here may import
``presenters``, ``interfaces`` or the MCP SDK. ``core`` is the only layer below.

Singular ``domain`` on purpose: these are the parts of one domain, not a pile of
separate ones.
"""
