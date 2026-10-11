# -*- coding: utf-8 -*-
"""Generación de listas M3U para los clientes de streaming."""

from __future__ import annotations

from . import config, util


def _escape(value):
    return util.sanitize_text(value, 256).replace('"', "'")


def channel_allowed(channel, account):
    """`account` es el diccionario de token del agente (o None para público)."""
    if not channel.get("enabled", True):
        return False
    if account is None:
        return False
    if account.get("all_channels"):
        return True
    allowed = account.get("channels") or []
    return channel.get("id") in allowed


def stream_url(cfg, token, channel_id, host=None):
    base = config.stream_base_url(cfg, host)
    return "%s/live/%s/%s.ts" % (base, token, channel_id)


def playlist(channels, token, account, cfg, host=None):
    """Lista M3U de los canales permitidos para ese token."""
    lines = ["#EXTM3U"]
    for channel in channels:
        if not channel_allowed(channel, account):
            continue
        lines.append('#EXTINF:-1 tvg-id="%s" tvg-logo="%s" group-title="%s",%s' % (
            _escape(channel.get("epg_id")),
            _escape(channel.get("logo")),
            _escape(channel.get("group") or "General"),
            _escape(channel.get("name")),
        ))
        lines.append(stream_url(cfg, token, channel.get("id"), host))
    return "\n".join(lines) + "\n"


def public_playlist(channels, cfg, host=None):
    """Lista de las salidas marcadas como públicas (sin token)."""
    lines = ["#EXTM3U"]
    base = config.stream_base_url(cfg, host)
    for channel in channels:
        if not channel.get("enabled", True):
            continue
        for output in channel.get("outputs") or []:
            if output.get("type") == "http" and output.get("public"):
                lines.append('#EXTINF:-1 tvg-id="%s" tvg-logo="%s" group-title="%s",%s' % (
                    _escape(channel.get("epg_id")),
                    _escape(channel.get("logo")),
                    _escape(channel.get("group") or "General"),
                    _escape(channel.get("name")),
                ))
                lines.append(base + output.get("uri"))
    return "\n".join(lines) + "\n"


def channels_to_m3u(channels, cfg, host=None, token=None):
    """Exportación de la configuración de canales como M3U (con su entrada)."""
    lines = ["#EXTM3U"]
    for channel in channels:
        url = ""
        for source in channel.get("inputs") or []:
            if source.get("type") == "http":
                url = source.get("url", "")
                break
        if not url and token:
            for output in channel.get("outputs") or []:
                if output.get("type") == "http":
                    url = stream_url(cfg, token, channel.get("id"), host)
                    break
        lines.append('#EXTINF:-1 tvg-id="%s" tvg-logo="%s" group-title="%s",%s' % (
            _escape(channel.get("epg_id")),
            _escape(channel.get("logo")),
            _escape(channel.get("group") or "General"),
            _escape(channel.get("name")),
        ))
        lines.append(url)
    return "\n".join(lines) + "\n"
