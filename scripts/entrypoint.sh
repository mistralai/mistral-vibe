#!/bin/sh

PUID=${PUID:-1000}
PGID=${PGID:-1000}

# update vibeuser's IDs to host suer's ID
current_uid=$(id -u vibeuser)
current_gid=$(id -g vibeuser)

if [ "$current_uid" -ne "$PUID" ]; then
    echo "Updating vibeuser UID from $current_uid to $PUID"
    usermod -o -u "$PUID" vibeuser
fi

if [ "$current_gid" -ne "$PGID" ]; then
    echo "Updating vibeuser GID from $current_gid to $PGID"
    groupmod -o -g "$PGID" vibeuser
fi

chown -R vibeuser:vibeuser /app

# import .vibe directory and adapt log session
cp -R /vibe_in/* /vibe/
chown -R vibeuser:vibeuser /vibe/

CONFIG_FILE="/vibe/config.toml"
if [ -f "$CONFIG_FILE" ]; then
    NEW_PATH="/logs/"
    sed -i "s|^save_dir = .*|save_dir = \"$NEW_PATH\"|" "$CONFIG_FILE"
fi

# execute docker CMD as vibeuser
exec gosu vibeuser "$@"
