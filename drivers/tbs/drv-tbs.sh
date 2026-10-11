#!/bin/bash
if [ "$EUID" -ne 0 ]
  then echo "Please run as root brooo!"
  exit
fi
echo 'Prepare system' 
if [ -n "`which apt-get`" ]; then apt-get -y install build-essential patchutils\
 libproc-processtable-perl linux-headers-$(uname -r) git;
systemctl disable apt-daily.service
systemctl disable apt-daily.timer ;
elif [ -n "`which yum`" ]; then yum -y group install "Development Tools"; 
yum -y install perl-CPAN "kernel-devel-uname-r == $(uname -r)" kernel-headers wget perl-core elfutils-libelf-devel;
(echo y;echo o conf prerequisites_policy follow;echo o conf commit)|cpan;
cpan Proc::ProcessTable;
cpan Digest::SHA; fi
rm -rf /usr/src/media_build
rm -rf /usr/src/media
rm -r /usr/src/dvb-firmwares.tar.bz2
cd /usr/src
git clone https://github.com/tbsdtv/media_build.git
git clone --depth=1 https://github.com/tbsdtv/linux_media.git -b latest ./media
cd media_build
make dir DIR=../media
make allyesconfig
make -j4
make install
wget https://master.dl.sourceforge.net/project/my-file/dvb-firmwares.tar.bz2
tar jxvf dvb-firmwares.tar.bz2 -C /lib/firmware/
echo 'Done! Please reboot the server'
