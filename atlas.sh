#!/bin/bash
# Insparation
# https://gist.github.com/bradp/bea76b16d3325f5c47d4
# https://gist.github.com/vraravam/5e28ca1720c9dddacdc0e6db61e093fe

# http://redsymbol.net/articles/unofficial-bash-strict-mode/
#set -euo pipefail
# `set -eu` causes an 'unbound variable' error in case SUDO_USER is not set

####
#
# Config
#
####

SUDO_USER=$(whoami)
TIMESTAMP=$(date +%s)
LOGFILE="./atlas-setup-$TIMESTAMP.log"
DIRS=(
    ~/.mackup
    ~/.nvm
    ~/GIT
    ~/GIT/_Apps
    ~/GIT/_Perso
    ~/GIT/_Hipages
    ~/GIT/_Stj
    ~/GIT/_Airbyte
)
KILLAPPS=(
    Finder
    Dock
    Mail
    Safari
    iTunes
    iCal
    Address\ Book
    SystemUIServer

)
PACKAGES=(
    awscli
    ca-certificates
    coreutils
    curl
    docker-compose
    git
    github/gh/gh
    git-lfs
    go
    gpg
    gradle
    helm
    htop
    jq
    kubectl
    kubernetes-cli
    kubernetes-helm
    lynx
    make
    mackup
    minikube
    neovim
    nmap
    node
    nodenv
    npm
    nvm
    openssl
    pre-commit
    pyenv
    pyenv-virtualenv
    pipenv
    rbenv
    readline
    speedtest-cli
    sqlite3
    terraform
    terraformer
    tldr
    tmux
    trash
    tree
    vim
    watch
    wget
    xz
    yamllint
    zlib
)
CASKS=(
    aerial
    airbuddy
    alfred
    amazon-chime
    brave-browser
    charles
    cheatsheet
    datagrip
    discord
    docker
    dropbox
    espanso
    firefox
    font-fira-code
    github
    gpg-suite
    iterm2
    keybase
    keycastr
    keyboard-maestro
    lastpass
    logseq
    loom
    mamp
    microsoft-office
    miro
    mysqlworkbench
    netnewswire
    notion
    obsidian
    onyx
    postman
    profilecreator
    rescuetime
    slack
    spotify
    sublime-text
    the-unarchiver
    transmit
    visual-studio-code
    vlc
    zoom
)
####
#
# Start Setup
#
####

echo -e "\033[0;33m"
cat << "EOF"
              ________
          ,o88~~88888888o,
        ,~~?8P  88888     8,
       d  d88 d88 d8_88     b
      d  d888888888          b
      8,?88888888  d8.b o.   8
      8~88888888~ ~^8888\ db 8
      ?  888888          ,888P
       ?  `8888b,_      d888P
        `   8888888b   ,888'
          ~-?8888888 _.P-~ 
            ~~  ~~  ~~
        __ _| |_| | __ _ ___ 
       / _` | __| |/ _` / __|
      | (_| | |_| | (_| \__ \
       \__,_|\__|_|\__,_|___/

 Welcome to atlas

 atlas is an automated script to help speed up
 development on a mac machine by scafholding 
 all your key development apps, settings, dotfiles
 configration and have you up and running in no
 time. Developed by Vincent Koc (@koconder)

 To update some of the "Defaults" feel free to
 modify this script to your liking. This script
 assumes the iCloud as the primary location for
 dotfiles and configration.

 Starting atlas...

EOF
echo -e "\033[0;36mPlease provide local password (may auto-skip)...\033[0m"
sudo -v

echo -e "\033[0;36mChecking to see if iCloud drive has been mounted...\033[0m"
if [ -d "~/Library/Mobile\ Documents/com~apple~CloudDocs/" ] 
then
    echo "Directory /path/to/dir exists." 
else
    echo "Error: Directory /path/to/dir does not exists."
fi


echo -e "\033[0;36mSetting up directories...\033[0m"
mkdir -p ${DIRS[@]} && 

# Close any open System Preferences panes, to prevent them from overriding
# settings we're about to change
osascript -e 'tell application "System Preferences" to quit'

# Ask for the administrator password upfront

# Keep-alive: update existing `sudo` time stamp until `.macos` has finished
while true; do sudo -n true; sleep 60; kill -0 "$$" || exit; done 2>/dev/null &

# Set UUID for plists
if [[ `ioreg -rd1 -c IOPlatformExpertDevice | grep -i "UUID" | cut -c27-50` != "00000000-0000-1000-8000-" ]]; then
    macUUID=`ioreg -rd1 -c IOPlatformExpertDevice | grep -i "UUID" | cut -c27-62`
fi

#Reveal system info (IP address, hostname, OS version, etc.) when clicking the clock in the login screen
sudo defaults write /Library/Preferences/com.apple.loginwindow AdminHostInfo HostName

#"Scroll direction not natural"
defaults write -g com.apple.swipescrolldirection -bool NO

#"Expanding the save panel by default"
defaults write NSGlobalDomain NSNavPanelExpandedStateForSaveMode -bool true
defaults write NSGlobalDomain PMPrintingExpandedStateForPrint -bool true
defaults write NSGlobalDomain PMPrintingExpandedStateForPrint2 -bool true

# Disable the “reopen windows when logging back in” option
# This works, although the checkbox will still appear to be checked,
# and the command needs to be entered again for every restart.
defaults write com.apple.loginwindow TALLogoutSavesState -bool false
defaults write com.apple.loginwindow LoginwindowLaunchesRelaunchApps -bool false

# Enable full keyboard access for all controls (e.g. enable Tab in modal dialogs)
defaults write NSGlobalDomain AppleKeyboardUIMode -int 3

# Require password immediately after sleep or screen saver begins
defaults write com.apple.screensaver askForPassword -int 1
defaults write com.apple.screensaver askForPasswordDelay -int 0

# Show status bar in Finder
defaults write com.apple.finder ShowStatusBar -bool true

#"Allow text selection in Quick Look"
defaults write com.apple.finder QLEnableTextSelection -bool TRUE

#"Automatically quit printer app once the print jobs complete"
defaults write com.apple.print.PrintingPrefs "Quit When Finished" -bool true

#"Check for software updates daily, not just once per week"
defaults write com.apple.SoftwareUpdate ScheduleFrequency -int 1

#"Enabling subpixel font rendering on non-Apple LCDs"
defaults write NSGlobalDomain AppleFontSmoothing -int 2

#"Showing icons for hard drives, servers, and removable media on the desktop"
defaults write com.apple.finder ShowExternalHardDrivesOnDesktop -bool true

#"Disabling the warning when changing a file extension"
defaults write com.apple.finder FXEnableExtensionChangeWarning -bool false

# Smaller sidebar icons
defaults write NSGlobalDomain "NSTableViewDefaultSizeMode" -int "1"

# Show the ~/Library folder
chflags nohidden ~/Library

#"Use column view in all Finder windows by default"
defaults write com.apple.finder FXPreferredViewStyle -string "clmv"

# Folders Ontop
defaults write com.apple.finder "_FXSortFoldersFirst" -bool "true"
defaults write com.apple.finder "_FXSortFoldersFirstOnDesktop" -bool "true"

# Search Scope set to current folder
defaults write com.apple.finder "FXDefaultSearchScope" -string "SCcf"

#"Showing all filename extensions in Finder by default"
defaults write NSGlobalDomain AppleShowAllExtensions -bool true
defaults write com.apple.Finder AppleShowAllFiles 1

#"Avoiding the creation of .DS_Store files on network volumes"
defaults write com.apple.desktopservices DSDontWriteNetworkStores -bool true

#"Enabling snap-to-grid for icons on the desktop and in other icon views"
/usr/libexec/PlistBuddy -c "Set :DesktopViewSettings:IconViewSettings:arrangeBy grid" ~/Library/Preferences/com.apple.finder.plist
/usr/libexec/PlistBuddy -c "Set :FK_StandardViewSettings:IconViewSettings:arrangeBy grid" ~/Library/Preferences/com.apple.finder.plist
/usr/libexec/PlistBuddy -c "Set :StandardViewSettings:IconViewSettings:arrangeBy grid" ~/Library/Preferences/com.apple.finder.plist

#"Setting Dock to auto-hide and removing the auto-hiding delay"
defaults write com.apple.dock autohide -bool true
defaults write com.apple.dock autohide-delay -float 0
defaults write com.apple.dock autohide-time-modifier -float "0.5"
defaults write com.apple.dock "tilesize" -int "36"

#"Setting email addresses to copy as 'foo@example.com' instead of 'Foo Bar <foo@example.com>' in Mail.app"
defaults write com.apple.mail AddressesIncludeNameOnPasteboard -bool false

#"Enabling UTF-8 ONLY in Terminal.app and setting the Pro theme by default"
defaults write com.apple.terminal StringEncodings -array 4
defaults write com.apple.Terminal "Default Window Settings" -string "Pro"
defaults write com.apple.Terminal "Startup Window Settings" -string "Pro"

#"Preventing Time Machine from prompting to use new hard drives as backup volume"
defaults write com.apple.TimeMachine DoNotOfferNewDisksForBackup -bool true

#"Speeding up wake from sleep to 24 hours from an hour"
# http://www.cultofmac.com/221392/quick-hack-speeds-up-retina-macbooks-wake-from-sleep-os-x-tips/
# sudo pmset -a standbydelay 86400

#"Setting screenshot format to PNG"
defaults write com.apple.screencapture type -string "png"

# Disable shadow in screenshots
defaults write com.apple.screencapture disable-shadow -bool true

#"Hiding Safari's sidebar in Top Sites"
defaults write com.apple.Safari ShowSidebarInTopSites -bool false

#"Disabling Safari's thumbnail cache for History and Top Sites"
defaults write com.apple.Safari DebugSnapshotsUpdatePolicy -int 2

#"Enabling Safari's debug menu"
defaults write com.apple.Safari IncludeInternalDebugMenu -bool true

#"Enabling the Develop menu and the Web Inspector in Safari"
defaults write com.apple.Safari IncludeDevelopMenu -bool true
defaults write com.apple.Safari WebKitDeveloperExtrasEnabledPreferenceKey -bool true
defaults write com.apple.Safari "com.apple.Safari.ContentPageGroupIdentifier.WebKit2DeveloperExtrasEnabled" -bool true

# Safari Show Full URL
defaults write com.apple.safari "ShowFullURLInSmartSearchField" -bool "true"

#"Adding a context menu item for showing the Web Inspector in web views"
defaults write NSGlobalDomain WebKitDeveloperExtras -bool true

## Allow pressing and holding a key to repeat it in VS Code - https://stackoverflow.com/questions/39972335/how-do-i-press-and-hold-a-key-and-have-it-repeat-in-vscode
defaults write com.microsoft.VSCode ApplePressAndHoldEnabled -bool false

# Default Temperature units
defaults write -g AppleTemperatureUnit -string "Celsius"

# Disable Guest User
sudo sysadminctl -guestAccount off
sudo defaults write /Library/Preferences/com.apple.loginwindow GuestEnabled -bool false

# Battery Bar
#defaults write com.apple.menuextra.battery ShowTime -string "YES"
defaults write com.apple.menuextra.battery ShowPercent -string "YES"

#Disable the sound effects on boot
sudo nvram SystemAudioVolume=" "

#Disable quartine on download messages
defaults write com.apple.LaunchServices LSQuarantine -bool false

# Remove duplicates in the 'Open With' menu (also see 'lscleanup' alias)
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -kill -r -domain local -domain system -domain user

# Disable Crash Report
defaults write com.apple.CrashReporter DialogType -string "none"

# Text Editor
defaults write com.apple.TextEdit RichText -int 0
defaults write com.apple.TextEdit PlainTextEncoding -int 4
defaults write com.apple.TextEdit PlainTextEncodingForWrite -int 4

# When switching applications, switch to respective space
defaults write -g AppleSpacesSwitchOnActivate -bool true

# Contacts
defaults write com.apple.AddressBook ABBirthDayVisible -bool true
defaults write com.apple.AddressBook ABDefaultAddressCountryCode -string au

# Unarchiver
defaults write com.macpaw.site.theunarchiver openExtractedFolder -bool true

# Docker
defaults write com.docker.docker SUAutomaticallyUpdate -bool true
defaults write com.docker.docker SUEnableAutomaticChecks -bool true

# Mail
defaults write com.apple.mail DraftsViewerAttributes -dict-add "DisplayInThreadedMode" -string "yes"
defaults write com.apple.mail DraftsViewerAttributes -dict-add "SortedDescending" -string "yes"
defaults write com.apple.mail DraftsViewerAttributes -dict-add "SortOrder" -string "received-date"

# Dark Mode
defaults write "Apple Global Domain" "AppleInterfaceStyle" "Dark"

# Hide spotlight from Menu
defaults -currentHost write com.apple.Spotlight MenuItemHidden -int 1

# Disable Siri
defaults write com.apple.Siri "UserHasDeclinedEnable" -bool true
defaults write com.apple.Siri "StatusMenuVisible" -bool false
defaults write com.apple.assistant.support "Assistant Enabled" -bool false

# Menu Bar
defaults write com.apple.menuextra.clock "DateFormat" -string "\"EEE d MMM HH:mm\""
defaults write com.apple.controlcenter "NSStatusItem Visible WiFi" -bool false

# Show Path Bar
defaults write com.apple.finder "ShowPathbar" -bool "true"

# Kill affected applications

killall ${KILLAPPS[@]}

# echo "Setting up Touch ID for sudo..."
# read -p "Press [Enter] key after this..."

# Install xcode
xcode-select --install > /dev/null 2>&1
if [ 0 == $? ]; then
    sleep 1
    osascript <<EOD
tell application "System Events"
    tell process "Install Command Line Developer Tools"
        keystroke return
        click button "Agree" of window "License Agreement"
    end tell
end tell
read -p "Press [Enter] key after this..."
EOD
else
    echo "Command Line Developer Tools are already installed!"
fi

# find the CLI Tools update
echo "Installing Mac OSX Updates..."
sudo softwareupdate --install --all

# Check for Homebrew
# Install if we don't have it
if test ! $(which brew); then
  echo "Installing homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
else
  echo "Homebrew installed; updating..."
  brew update
fi

# Brew update
echo "Updating homebrew..."
brew update
brew upgrade

# Homebrew casks
echo "Tapping homebrew casks..."
brew tap homebrew/cask
brew tap homebrew/cask-versions
brew tap homebrew/cask-fonts

# Install Mac OS App Store Apps
# echo "Installing Mac App Store Apps..."
# brew install mas
#
## 441258766   Magnet        (2.11.0)
## 937984704   Amphetamine   (5.2.2)
## 1564015476  ShellHistory  (2.0.0)
## 975937182   Fantastical   (3.7.6)
## 639968404   Parcel        (7.6.6)
#
#
# https://apps.apple.com/us/app/shellhistory/id1564015476
# https://apps.apple.com/au/app/magnet/id441258766?mt=12
# https://apps.apple.com/us/app/amphetamine/id937984704?mt=12
# https://apps.apple.com/us/app/parcel-delivery-tracking/id639968404?mt=12
# https://apps.apple.com/us/app/fantastical-2/id975937182?mt=12&xcust=1675244233370vlst&xs=1

# Install OpenJDK Java
echo "Installing Java (OpenJDK)..."
brew tap AdoptOpenJDK/openjdk
brew install --cask adoptopenjdk
brew install --cask adoptopenjdk11

# Install Homebrew packages

echo "Installing packages..."
brew install ${PACKAGES[@]}

# Set Default Screensaver
echo "Installing packages..."
defaults -currentHost write com.apple.screensaver idleTime 120
defaults -currentHost write com.apple.screensaver 
defaults -currentHost write com.apple.screensaver moduleDict -dict path -string "/Users/$SUDO_USER/Library/Screen Savers/Aerial.saver" moduleName -string "Aerial" type -int 0 

# Install Homebrew casks

echo "Installing cask apps..."
sudo -u $SUDO_USER brew install --appdir="/Applications" --cask ${CASKS[@]}

# Install Other Misc Apps with Homebrew...
echo "Installing Chat GPT client..."
brew tap lencx/chatgpt https://github.com/lencx/ChatGPT.git
sudo -u $SUDO_USER brew install --appdir="/Applications" --cask chatgpt --no-quarantine

# Brew Cleanup
echo "Cleaning homebrew..."
brew cleanup

# Setup Python
echo 'export PYENV_ROOT="$(pyenv root)"' >> ~/.zshrc
echo 'export PATH="$PYENV_ROOT/shims:$PATH"' >> ~/.zshrc
echo 'eval "$(pyenv init --path)"' >> ~/.zprofile
echo 'eval "$(pyenv init -)"' >> ~/.zshrc
which python
pyenv versions
pyenv install 3.9.11
pyenv global 3.9.11
pyenv versions
which python

# Mackup
if [ -L ~/.mackup.cfg ] ; then
    echo "mackup detected"
    mackup backup
else
    rm -rf ~/.mackup.cfg
    touch ~/.mackup.cfg
    cat <<EOT >> ~/.mackup.cfg
[storage]
engine = icloud
directory = dotfiles
EOT
    #mackup restore
fi

# Brew Doctor
echo "Checking homebrew..."
brew doctor
