"""The MCP interface: the FastMCP app, and the tool modules that register against it.

The package is called ``mcp`` and so is the SDK it imports. That is safe and not a
near miss: ``from mcp.server.fastmcp import FastMCP`` inside ``app`` is an absolute
import, and absolute imports have never resolved to sibling submodules. The two
live under different ``sys.modules`` keys -- ``mcp`` and ``satisfactory_mcp.interfaces.mcp``.

Import-free, like ``interfaces`` above it: importing ``tools`` is what runs the
decorators, and that should stay an explicit act by ``server``.
"""
