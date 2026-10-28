#ifndef TIMELM_PAYLOAD_H
#define TIMELM_PAYLOAD_H
/* SPDX-License-Identifier: BSD-2-Clause */
static unsigned int TimelmRead32(const unsigned char *p)
{
  return (unsigned int)p[0] | ((unsigned int)p[1] << 8) |
         ((unsigned int)p[2] << 16) | ((unsigned int)p[3] << 24);
}
static int TimelmFindLoader(const unsigned char *p, unsigned int available,
                           const unsigned char **image, unsigned int *length)
{
  unsigned int size, at = 8;
  static const char name[] = "loader.efi";
  *image = 0; *length = 0;
  if (available < 8 || TimelmRead32(p) != 0x50554e58U) return 0;
  size = TimelmRead32(p + 4);
  if (size > 0x03fffff8U || size > available - 8) return 0;
  unsigned int end = size + 8;
  for (unsigned int count = 0; count < 128 && end - at >= 24; count++) {
    unsigned int bytes = TimelmRead32(p + at + 16), match = 1;
    if (p[at] == 0) return 0;
    if (bytes > end - at - 24) return 0;
    for (unsigned int i = 0; i < sizeof(name); i++)
      if (p[at + i] != (unsigned char)name[i]) match = 0;
    if (match && bytes) { *image = p + at + 24; *length = bytes; return 1; }
    unsigned int padded = (bytes + 15U) & ~15U;
    if (padded > end - at - 24) return 0;
    at += 24 + padded;
  }
  return 0;
}
#endif
