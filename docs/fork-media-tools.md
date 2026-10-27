# Metadata repair and Video Sync

Both tools appear in the **Maintenance** sidebar. Tidal is omitted from this port.

## Repair Metadata

Load an iPod Library and open **Repair Metadata**. Choose MusicBrainz or Apple
iTunes, then **Scan Library**. Each lookup sends album and artist names to the
selected service; opening the page never starts a lookup automatically.

The default fills missing track/disc numbers, totals and credits while preserving
existing values. **Replace existing values** explicitly permits replacement and
matching by existing disc/track position. **Ignore version annotations** permits
titles with labels such as Remastered; ambiguous matches are skipped unless a
filename resolves the position.

Review the changes and match evidence, select tracks, and choose **Apply selected
to Draft**. Filtering limits the visible selection. Use **Review Changes** to save
through the ordinary Library workflow. Edits, reloads, disconnection and locking
invalidate pending suggestions. Per-album lookup failures leave the Library intact.

## Video Sync

Select a video-capable iPod to capture its encoding limits. Open **Video Sync**,
paste a single video URL, choose Music Video, Movie or TV Show, and add it to the
queue. Playlists are not expanded.

The default fits the iPod screen. **TV out resolution** permits its full supported
dimensions. Both constrain H.264 to level 3.0 or lower and audio to the device
limit, capped at 160 kbps. FFmpeg and FFprobe use the existing Media Tools settings.
The new `yt-dlp` dependency with its recommended extras provides site extraction
and downloading; conversion and validation use native media preparation. Current
YouTube support also needs a supported JavaScript runtime such as Deno on PATH.
The extras include its challenge scripts; they do not install the runtime.

Completed videos persist in the configuration directory's `VideoSync` folder,
shown on the page. Partial work is private and cleaned up. **Cancel downloads**
clears queued work and stops the active job at its next checkpoint; an in-flight
request may take until its timeout to return. **Retry selected** requeues a failed
or cancelled URL with the currently selected iPod's capabilities.

Select cached videos and choose **Review sync to iPod** to scan those files and
open the normal Sync Workspace. Downloads never write directly to a device.
Confirmed deletion removes only Host downloads; device copies remain intact.

Automated tests mock network responses and generate local FFmpeg fixtures. Live
site availability, frozen builds and physical-device playback need manual testing.
