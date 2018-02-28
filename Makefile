# Hey emacs, use -*-Makefile-*- mode
######################################################################
# $Id: Makefile.dist,v 1.8 2003/07/29 17:00:07 edwinh Exp $
# $Name: v1_2_1 $
######################################################################

PKG=flexbackup-manager

VERSION=0.0.1

PKGFULL=${PKG}-${VERSION}

PREFIX=/usr

# Where the script binary should go
BINPATH = $(PREFIX)/bin

ETCPATH = /etc

all: install

install: all
	install -D -m 0644 flexbackup.conf.tmpl $(ETCPATH)/flexbackup-manager/flexbackup.conf.tmpl
	install -D -m 0644 flexbackup-manager-conf.yaml $(ETCPATH)/flexbackup-manager/flexbackup-conf.yaml
	install -m 0755 flexbackup-manager $(BINPATH)/flexbackup-manager

${PKGFULL}.orig.tar.gz: ${PKGFULL}
	@tar zcf $@ $<

${PKGFULL}:
	@mkdir -p $@
	@cp -a flexbackup-manager flexbackup-manager-conf.yaml flexbackup.conf.tmpl setup.py packaging/debian $@
	@cp -a services/* $@

debian: ${PKG}-${VERSION} ${PKG}-${VERSION}.orig.tar.gz
	@cd ${PKG}-${VERSION} && debuild -us -uc -b

clean:
	rm -rf *.changes *.build *.deb ${PKGFULL}*
