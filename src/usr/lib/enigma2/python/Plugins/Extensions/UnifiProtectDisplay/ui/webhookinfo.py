# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""The addresses to paste into Protect's Alarm Manager.

Setting motion up means copying a URL per camera into Protect, and the user
is sitting in front of a television, not a terminal. So the URLs are shown
here in full, on their own lines, rather than squeezed into a settings row
where a long address would simply be cut off.

Nothing is editable: the port and the token come from the settings, and the
receiver's own address is asked of the routing table, so what is shown is
what Protect will actually have to call.
"""

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MenuList import MenuList
from Components.Sources.StaticText import StaticText
from Screens.Screen import Screen

from ..localisation import _


class UnifiProtectWebhookInfo(Screen):
	skin = """
		<screen name="UnifiProtectWebhookInfo" position="center,center"
				size="1060,600" title="Motion webhook addresses">
			<widget name="intro" position="10,8" size="1040,86"
					font="Regular;22" foregroundColor="#cccccc" />
			<widget name="list" position="10,100" size="1040,370"
					scrollbarMode="showOnDemand" font="Regular;20"
					itemHeight="30" />
			<widget name="hint" position="10,478" size="1040,60"
					font="Regular;20" foregroundColor="#ffcc66" />
			<widget source="key_red" render="Label" position="10,548" size="250,40"
					backgroundColor="#9f1313" font="Regular;22" halign="center"
					valign="center" />
			<widget source="key_green" render="Label" position="270,548" size="320,40"
					backgroundColor="#1f771f" font="Regular;22" halign="center"
					valign="center" />
		</screen>"""

	def __init__(self, session, manager):
		Screen.__init__(self, session)
		self.setTitle(_("Motion webhook addresses"))
		self._manager = manager

		self["intro"] = Label(_(
			"In UniFi Protect: Alarm Manager > New Alarm, one per camera.\n"
			"Trigger Motion, scope that camera, action Webhook > Custom "
			"Webhook, method GET,\nand paste the matching address below."))
		self["list"] = MenuList([])
		self["hint"] = Label("")
		self["key_red"] = StaticText(_("Close"))
		self["key_green"] = StaticText(_("New token"))

		self["actions"] = ActionMap(
			["OkCancelActions", "ColorActions"], {
				"cancel": self.close,
				"ok": self.close,
				"red": self.close,
				"green": self._new_token,
			}, -1)

		self.onLayoutFinish.append(self._rebuild)

	def _rebuild(self):
		from ..settings import get_config
		config = get_config()
		rows = []
		if not config.webhook.value:
			self["hint"].setText(_(
				"Motion reporting is switched off. Turn on "
				"\"React to motion reported by Protect\" in settings first."))
		else:
			self["hint"].setText(_(
				"The token keeps a stray request from switching the "
				"television. Pressing Green issues a new one, which makes "
				"every address already pasted into Protect stop working."))
		for name, url in self._manager.webhook_urls():
			rows.append(name)
			rows.append("    " + url)
			rows.append("")
		if not rows:
			rows = [_("No cameras selected, or no token yet.")]
		self["list"].setList(rows)

	def _new_token(self):
		from ..webhook import generate_token
		from .. import settings as settings_module
		config = settings_module.get_config()
		config.webhook_token.value = generate_token()
		config.webhook_token.save()
		settings_module.save()
		# Restart the listener so it accepts the new token immediately.
		self._manager.stop_webhook()
		self._manager.start_webhook()
		self._rebuild()
