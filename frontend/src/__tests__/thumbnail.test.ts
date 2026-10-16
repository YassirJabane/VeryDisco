import { describe, expect, test } from 'vitest';
import { thumbnailUrl } from '../thumbnail';

describe('thumbnail URLs', () => {
  test('uses provider thumbnails while preserving the exact CAA image identity', () => {
    expect(thumbnailUrl('https://coverartarchive.org/release/abc/123456789-1200.jpg')).toBe('https://coverartarchive.org/release/abc/123456789-250.jpg');
    expect(thumbnailUrl('https://coverartarchive.org/release/abc/front')).toBe('https://coverartarchive.org/release/abc/front-250');
    expect(thumbnailUrl('https://cdn-images.dzcdn.net/images/cover/hash/1000x1000-000000-80-0-0.jpg')).toBe('https://cdn-images.dzcdn.net/images/cover/hash/250x250-000000-80-0-0.jpg');
    expect(thumbnailUrl('https://is1-ssl.mzstatic.com/image/thumb/abc/1200x1200bb.jpg')).toBe('https://is1-ssl.mzstatic.com/image/thumb/abc/250x250bb.jpg');
  });
  test('does not rewrite custom, signed or local image URLs', () => {
    expect(thumbnailUrl('https://example.org/1000x1000.jpg?signature=x')).toBe('https://example.org/1000x1000.jpg?signature=x');
    expect(thumbnailUrl('/api/library/albums/cover?folder_path=album')).toBe('/api/library/albums/cover?folder_path=album');
    expect(thumbnailUrl(undefined)).toBe('');
  });
});
