/** Resize only known provider URLs; leave custom/unknown URLs untouched. */
export function thumbnailUrl(value?: string): string {
  if (!value) return '';
  try {
    const url = new URL(value);
    if (url.hostname === 'cdn-images.dzcdn.net' || url.hostname === 'e-cdns-images.dzcdn.net') {
      url.pathname = url.pathname.replace(/\/\d+x\d+(-[^/]+)?\.(jpg|png|webp)$/i, '/250x250$1.$2');
    } else if (url.hostname === 'coverartarchive.org') {
      url.protocol = 'https:';
      url.pathname = url.pathname.replace(/\/front(?:-\d+)?$/, '/front-250')
        .replace(/\/(\d+)(?:-\d+)?\.jpg$/, '/$1-250.jpg');
    } else if (/^is\d+-ssl\.mzstatic\.com$/.test(url.hostname)) {
      url.pathname = url.pathname.replace(/\/\d+x\d+([^/]*)$/, '/250x250$1');
    }
    return url.href;
  } catch {
    return value;
  }
}
