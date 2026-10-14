# Installs the plugin for an OpenEmbedded recipe. For a plain .ipk use
# build.py instead; it needs nothing but a Python interpreter.

PLUGINDIR = $(libdir)/enigma2/python/Plugins/Extensions/UnifiProtectDisplay
SRCDIR = $(srcdir)/src/usr/lib/enigma2/python/Plugins/Extensions/UnifiProtectDisplay

LANGUAGES = de
POFILES = $(addprefix $(srcdir)/po/,$(addsuffix .po,$(LANGUAGES)))

EXTRA_DIST = \
	README.md CHANGELOG.md LICENSE build.py pytest.ini \
	$(POFILES) $(srcdir)/po/UnifiProtectDisplay.pot \
	$(srcdir)/docs $(srcdir)/tests $(srcdir)/tools $(srcdir)/CONTROL

install-data-local:
	for dir in . protect player render ui; do \
		$(MKDIR_P) "$(DESTDIR)$(PLUGINDIR)/$$dir"; \
		for file in $(SRCDIR)/$$dir/*.py; do \
			test -f "$$file" && $(INSTALL_DATA) "$$file" \
				"$(DESTDIR)$(PLUGINDIR)/$$dir/" || true; \
		done; \
	done
	$(INSTALL_DATA) $(SRCDIR)/plugin.png "$(DESTDIR)$(PLUGINDIR)/"
if HAVE_MSGFMT
	for lang in $(LANGUAGES); do \
		$(MKDIR_P) "$(DESTDIR)$(PLUGINDIR)/locale/$$lang/LC_MESSAGES"; \
		$(MSGFMT) -o \
			"$(DESTDIR)$(PLUGINDIR)/locale/$$lang/LC_MESSAGES/UnifiProtectDisplay.mo" \
			"$(srcdir)/po/$$lang.po"; \
	done
endif

uninstall-local:
	rm -rf "$(DESTDIR)$(PLUGINDIR)"

.PHONY: check-local
check-local:
	cd $(srcdir) && $(PYTHON) -m pytest -q
