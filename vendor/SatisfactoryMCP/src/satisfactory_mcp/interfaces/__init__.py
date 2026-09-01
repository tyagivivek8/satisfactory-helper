"""The outward-facing edges: one package per protocol the world talks to us in.

Nothing here holds a decision. An interface parses arguments, calls a domain service
and hands the result to a presenter -- so ``mcp`` and ``web`` can disagree about
wire format while agreeing, by construction, about what the answer is.

Import-free on purpose: ``interfaces.mcp`` builds a FastMCP app as an import side
effect, and a re-export here would make merely naming the package start it.
"""
