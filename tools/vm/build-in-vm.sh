#!/bin/bash
# Run the iOS root build inside an Alpine qemu guest.
#
#   tools/vm/build-in-vm.sh --img IMG --os-tar A --sysos-tar B --appos-tar C \
#                           --tools-tar D [--qemu PATH] [--guestdir DIR] \
#                           [--work DIR] [--mem MB]
#
# The guest boots the Alpine lts kernel and runs tools/vm/build-in-guest.sh
# as root.  The HFS+ image and the three archives are attached as plain
# virtio disks:
#
#   /dev/vda  the HFS+ image        /dev/vdc  sysos.tar
#   /dev/vdb  os.tar               /dev/vdd  appos.tar
#                                    /dev/vde  tools.tar
#
# The guest is driven over its serial console by tools/vm/vmrun.py.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

qemu=$(command -v qemu-system-x86_64 || true)
img=; os_tar=; sysos_tar=; appos_tar=; tools_tar=
guestdir=${GUESTDIR:-$HOME/work/kimg}
work=${WORK:-$HOME/work/iosroot-build}
mem=${MEM:-8192}
modloop_url=${MODLOOP_URL:-https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/x86_64/netboot/modloop-lts}

while [[ $# -gt 0 ]]; do
	case $1 in
	--qemu) qemu=$2; shift 2 ;;
	--img) img=$2; shift 2 ;;
	--os-tar) os_tar=$2; shift 2 ;;
	--sysos-tar) sysos_tar=$2; shift 2 ;;
	--appos-tar) appos_tar=$2; shift 2 ;;
	--tools-tar) tools_tar=$2; shift 2 ;;
	--guestdir) guestdir=$2; shift 2 ;;
	--work) work=$2; shift 2 ;;
	--mem) mem=$2; shift 2 ;;
	--modloop-url) modloop_url=$2; shift 2 ;;
	*) echo "unknown option $1" >&2; exit 2 ;;
	esac
done

[[ -n $qemu && -x $qemu ]] || { echo "Error: qemu-system-x86_64 not found" >&2; exit 1; }
for v in img os_tar sysos_tar appos_tar tools_tar; do
	[[ -n ${!v} ]] || { echo "Error: missing --${v//_/-}" >&2; exit 2; }
done

mkdir -p "$work"
sock=$work/vm.sock
conlog=$work/vm-console.log
cmds=$work/vm-commands.txt

cat > "$cmds" <<EOF
ip link set eth0 up; udhcpc -i eth0 -q >/dev/null 2>&1
echo "nameserver 10.0.2.3" > /etc/resolv.conf
apk add rsync python3 tar >/dev/null 2>&1; which rsync python3 tar
modprobe hfsplus; modprobe loop; echo modules_ok
mkdir -p /mnt/tools /mnt/root
tar xpf /dev/vde -C /mnt/tools && ls /mnt/tools/
sh /mnt/tools/build-in-guest.sh > /mnt/build.log 2>&1
grep -c BUILD_DONE /mnt/build.log
tail -5 /mnt/build.log
sync
poweroff
EOF

# The 9p module is loaded but unused: the guest's virtio-pci does not bind
# the modern 0x1111 9p device, so 9p never appears inside the guest.
append="console=ttyS0,115200 modloop=$modloop_url"
append="$append alpine_repo=https://dl-cdn.alpinelinux.org/alpine/v3.24/main"
append="$append repository=https://dl-cdn.alpinelinux.org/alpine/v3.24/community"
append="$append modules=loop,squashfs,sd-mod,usb-storage,virtio_pci_modern_dev,virtio_pci,virtio_blk,virtio_net,hfsplus,fuse"

rm -f "$sock"
setsid nohup "$qemu" \
	-m "$mem" -display none -monitor none -cpu max \
	-kernel "$guestdir/vmlinuz-lts" -initrd "$guestdir/initramfs-lts" \
	-append "$append" \
	-drive "file=$img,format=raw,if=virtio" \
	-drive "file=$os_tar,format=raw,if=virtio" \
	-drive "file=$sysos_tar,format=raw,if=virtio" \
	-drive "file=$appos_tar,format=raw,if=virtio" \
	-drive "file=$tools_tar,format=raw,if=virtio" \
	-chardev "socket,id=c0,path=$sock,server=on,wait=on" \
	-serial chardev:c0 \
	-netdev user,id=n0 -device virtio-net-pci,netdev=n0 \
	> "$work/vm-qemu.log" 2>&1 < /dev/null &
qemu_pid=$!
echo "qemu pid $qemu_pid"

trap 'kill $qemu_pid 2>/dev/null || true' EXIT

python3 "$here/vmrun.py" "$cmds" --sock "$sock" --log "$conlog" --boot-timeout 900
