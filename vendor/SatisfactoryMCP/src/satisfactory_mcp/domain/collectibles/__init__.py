"""What the map placed and what this save has taken off it.

``table`` is the map's own placement list -- the only thing that can name a class --
and ``removed`` is the save's destroyed-actor list joined against it. Keeping the
two apart is what keeps both honest: the save is never a source of position and the
table is never a source of state.
"""
