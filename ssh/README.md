# SSH Keys for WAGO Sync

This directory contains SSH keys for syncing data from the WAGO controller.

## Setup Instructions

1. Generate RSA SSH key (dropbear compatible):
   ```bash
   ssh-keygen -t rsa -b 4096 -f ./ssh/wago_sync -N ""
   ```

2. Copy public key to WAGO controller:
   ```bash
   ssh-copy-id -o PubkeyAcceptedAlgorithms=+ssh-rsa -i ./ssh/wago_sync.pub admin@192.168.1.10
   ```
   Enter password: `wago`

3. Test connection (with dropbear-compatible options):
   ```bash
   ssh -i ./ssh/wago_sync \
       -o PubkeyAcceptedAlgorithms=+ssh-rsa \
       -o HostKeyAlgorithms=+ssh-rsa \
       admin@192.168.1.10 "ls /media/sd/CSV_Files/"
   ```

## Dropbear Compatibility

The WAGO controller uses dropbear SSH server, which may not support newer RSA
signature algorithms (rsa-sha2-256/512). The options above force the use of
the legacy `ssh-rsa` algorithm.

## Security Note

The private key (`wago_sync`) is git-ignored for security. Do not commit private keys to version control.
