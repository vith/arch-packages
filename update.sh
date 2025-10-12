#!/bin/bash

set -e

# Get the latest release version from GitHub
latest_version=$(curl -s "https://api.github.com/repos/parnoldx/nascTUI/releases/latest" | grep -Po '"tag_name": "\K.*?(?=")' | sed 's/v//')

# Get the current version from the PKGBUILD
current_version=$(grep -oP 'pkgver=\K.*' PKGBUILD)

if [ "$latest_version" != "$current_version" ]; then
    echo "New version available: $latest_version"
    # Update the pkgver in the PKGBUILD
    sed -i "s/pkgver=.*/pkgver=$latest_version/" PKGBUILD
    # Update the checksums
    updpkgsums
    # Update the .SRCINFO file
    makepkg --printsrcinfo > .SRCINFO
    # Commit the changes
    git add PKGBUILD .SRCINFO
    git commit -m "chore(nasc-tui-bin): update to version $latest_version"
    echo "PKGBUILD updated to version $latest_version and committed."
else
    echo "Already up to date."
fi
