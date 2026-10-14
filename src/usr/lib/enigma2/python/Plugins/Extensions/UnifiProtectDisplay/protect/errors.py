# -*- coding: utf-8 -*-
# Copyright (C) 2026 empyfi
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exception types for the Protect client.

The split is by what the user has to *do* about it, not by HTTP status, because
the only consumer is a TV screen where the message has to be actionable with a
remote control in hand.
"""


class ProtectError(Exception):
	"""Base class. Carries a message already fit to show on screen."""


class ProtectAuthError(ProtectError):
	"""Key rejected (401/403). The user must issue a new key in Protect."""


class ProtectNotFoundError(ProtectError):
	"""404. Usually a camera that was removed from the console."""


class ProtectUnavailableError(ProtectError):
	"""Console unreachable, timed out, or returned 5xx. Retrying may help."""


class ProtectProtocolError(ProtectError):
	"""Reachable and authorised, but the response was not what we expect.

	Raised for malformed JSON or a missing field. Distinct from
	:class:`ProtectUnavailableError` because retrying will not fix it: it means
	the console runs a Protect version whose API differs from what we target.
	"""
