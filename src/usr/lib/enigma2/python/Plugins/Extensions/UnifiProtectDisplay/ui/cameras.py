# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Choosing which cameras to show, and in what order.

The list order is the rotation order, so it is editable here rather than
being an invisible consequence of whatever the console returned.
"""

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MenuList import MenuList
from Components.Sources.StaticText import StaticText
from Screens.Screen import Screen
from ..localisation import _

MARK_ON = "☑"  # ballot box with check
MARK_OFF = "☐"  # empty ballot box


class UnifiProtectCameraList(Screen):
	skin = """
		<screen name="UnifiProtectCameraList" position="center,center"
				size="900,560" title="Cameras">
			<widget name="list" position="10,10" size="880,420"
					scrollbarMode="showOnDemand" font="Regular;26"
					itemHeight="36" />
			<widget name="status" position="10,436" size="880,56"
					font="Regular;22" foregroundColor="#ffcc66" />
			<widget source="key_red" render="Label" position="10,505" size="215,40"
					backgroundColor="#9f1313" font="Regular;22" halign="center"
					valign="center" />
			<widget source="key_green" render="Label" position="235,505" size="215,40"
					backgroundColor="#1f771f" font="Regular;22" halign="center"
					valign="center" />
			<widget source="key_yellow" render="Label" position="460,505" size="215,40"
					backgroundColor="#a08500" font="Regular;22" halign="center"
					valign="center" />
			<widget source="key_blue" render="Label" position="685,505" size="205,40"
					backgroundColor="#18188b" font="Regular;22" halign="center"
					valign="center" />
		</screen>"""

	def __init__(self, session, manager):
		Screen.__init__(self, session)
		self.setTitle(_("Cameras"))
		self._manager = manager
		self._store = manager.store
		self._ids = []
		self._busy = False

		self["list"] = MenuList([])
		self["status"] = Label("")
		self["key_red"] = StaticText(_("Close"))
		self["key_green"] = StaticText(_("Refresh from console"))
		self["key_yellow"] = StaticText(_("Move up"))
		self["key_blue"] = StaticText(_("Move down"))

		self["actions"] = ActionMap(
			["OkCancelActions", "ColorActions"], {
				"ok": self._toggle,
				"cancel": self.close,
				"red": self.close,
				"green": self._refresh,
				"yellow": lambda: self._move(-1),
				"blue": lambda: self._move(1),
			}, -1)

		self.onLayoutFinish.append(self._first_show)

	# -- display ----------------------------------------------------------

	def _first_show(self):
		self._rebuild()
		if not len(self._store):
			# An empty list on first open is the normal case, not an error;
			# fetch rather than making the user guess what to press.
			self._refresh()

	def _rebuild(self, keep_index=True):
		index = self["list"].getCurrentIndex() if keep_index else 0
		rows = []
		self._ids = []
		for camera_id, name, enabled in self._store.all():
			# A camera that is offline in Protect shows nothing but black on
			# the television, which is indistinguishable from a bug. Say so
			# here instead of letting the user hunt for it.
			suffix = "" if self._store.is_connected(camera_id) else _("  (offline)")
			rows.append("%s  %s%s" % (
				MARK_ON if enabled else MARK_OFF, name, suffix))
			self._ids.append(camera_id)
		self["list"].setList(rows)
		if rows:
			self["list"].setCurrentIndex(max(0, min(index, len(rows) - 1)))
		self._update_status()

	def _update_status(self):
		if self._busy:
			return
		total = len(self._ids)
		selected = len(self._store.enabled_ids())
		if not total:
			self["status"].setText(
				_("No cameras known yet. Press Green to fetch them."))
		else:
			self["status"].setText(
				_("%d of %d selected. OK toggles, Yellow/Blue reorder.")
				% (selected, total))

	def _current_id(self):
		index = self["list"].getCurrentIndex()
		if index is None or index < 0 or index >= len(self._ids):
			return None
		return self._ids[index]

	# -- actions ----------------------------------------------------------

	def _toggle(self):
		camera_id = self._current_id()
		if camera_id is None:
			return
		self._store.toggle(camera_id)
		self._store.save()
		self._rebuild()
		# Apply immediately: a running corner should reflect the change
		# without the user having to leave and come back.
		self._manager.refresh_selection()

	def _move(self, offset):
		camera_id = self._current_id()
		if camera_id is None:
			return
		if not self._store.move(camera_id, offset):
			return
		self._store.save()
		index = self["list"].getCurrentIndex()
		self._rebuild(keep_index=False)
		self["list"].setCurrentIndex(
			max(0, min(index + offset, len(self._ids) - 1)))
		self._manager.refresh_selection()

	def _refresh(self):
		if self._busy:
			return
		self._busy = True
		self["status"].setText(_("Asking the console for cameras..."))
		self._manager.controller.refresh_cameras(
			on_done=self._refreshed, on_error=self._refresh_failed)

	def _refreshed(self, counts):
		self._busy = False
		added, removed = counts
		self._rebuild()
		if added or removed:
			# New cameras arrive switched off on purpose, so say so --
			# otherwise it looks as though the refresh did nothing.
			self["status"].setText(
				_("%d new, %d removed. New cameras start switched off.")
				% (added, removed))
		else:
			self["status"].setText(_("Camera list is up to date."))
		self._manager.refresh_selection()

	def _refresh_failed(self, exc):
		self._busy = False
		self["status"].setText(self._manager.controller.describe_error(exc))
