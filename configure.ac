AC_INIT([enigma2-plugin-extensions-unifi-protect-display], [0.1.0], [https://github.com/empyfi/UniFiProtectDisplay])
AM_INIT_AUTOMAKE([foreign no-define subdir-objects])
AC_CONFIG_MACRO_DIRS([m4])

AC_PREFIX_DEFAULT([/usr])

dnl The plugin is pure Python, so there is nothing to compile; the build only
dnl installs sources and compiles the gettext catalogues.
AM_PATH_PYTHON([3.0])
AC_PATH_PROG([MSGFMT], [msgfmt], [no])
AS_IF([test "x$MSGFMT" = "xno"],
	[AC_MSG_WARN([msgfmt not found: translations will not be installed])])
AM_CONDITIONAL([HAVE_MSGFMT], [test "x$MSGFMT" != "xno"])

AC_CONFIG_FILES([Makefile])
AC_OUTPUT
