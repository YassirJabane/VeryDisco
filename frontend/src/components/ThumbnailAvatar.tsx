import React from 'react';
import { Avatar, AvatarProps } from '@mui/material';
import { thumbnailUrl } from '../thumbnail';
import { resolveTrackArtwork, trackKey } from '../trackArtwork';

/** One policy for list/card artwork, including URLs saved before thumbnails. */
export default function ThumbnailAvatar({ src, imgProps, track, ...props }: AvatarProps & { track?: any }) {
  const primary = thumbnailUrl(src);
  const element = React.useRef<HTMLDivElement>(null);
  const [visible, setVisible] = React.useState(typeof IntersectionObserver === 'undefined');
  React.useEffect(() => {
    if (visible || !element.current) return;
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) {
        setVisible(true);
        observer.disconnect();
      }
    }, { rootMargin: '200px' });
    observer.observe(element.current);
    return () => observer.disconnect();
  }, [visible]);
  const [failed, setFailed] = React.useState('');
  const [fallback, setFallback] = React.useState({ source: '', url: '' });
  React.useEffect(() => {
    if (!track || failed !== primary || !primary.startsWith('https://coverartarchive.org/')) return;
    let cancelled = false;
    const missing = { ...track, artwork: '', cover_medium: '', cover_small: '', cover: '', image_url: '' };
    resolveTrackArtwork([missing]).then(covers => {
      if (!cancelled) setFallback({ source: primary, url: covers.get(trackKey(missing)) || '' });
    });
    return () => { cancelled = true; };
  }, [failed, primary, track?.artist, track?.title, track?.album, track?.recording_mbid, track?.mbid]);
  // MUI preloads src internally, so native loading="lazy" alone isn't enough.
  return <Avatar {...props} ref={element} src={visible ? (fallback.source === primary ? fallback.url : primary) : undefined} imgProps={{
    loading: 'lazy', decoding: 'async', ...imgProps,
    onError: event => { setFailed(primary); imgProps?.onError?.(event); },
  }} />;
}
