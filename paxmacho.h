/*
 * Copyright 2005 Gentoo Foundation
 * Distributed under the terms of the GNU General Public License v2
 * $Header: /var/cvsroot/gentoo-projects/pax-utils/paxmacho.h,v 1.1 2005/11/02 04:23:21 vapier Exp $
 *
 * Copyright 2005 Ned Ludd        - <solar@gentoo.org>
 * Copyright 2005 Mike Frysinger  - <vapier@gentoo.org>
 *
 * Make sure all of the common mach-o stuff is setup as we expect
 */

#ifndef _PAX_MACHO_H
#define _PAX_MACHO_H

typedef struct {
	void *mhdr;
	char *data;
	uint32_t macho_class;
	off_t len;
	int fd;
	const char *filename;
	const char *base_filename;
} machoobj;

machoobj *readmacho(const char *filename);
void unreadmacho(machoobj *macho);
const char *get_machomhtype(int mh_type);

#endif /* _PAX_MACHO_H */
