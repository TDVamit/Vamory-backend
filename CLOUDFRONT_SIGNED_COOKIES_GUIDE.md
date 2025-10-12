# CloudFront Signed Cookies Implementation Guide

## Overview

This implementation provides CloudFront signed cookies authentication for three CDN endpoints:
1. `GET /api/v1/cdn/urls` - Get paginated CDN URLs
2. `GET /api/v1/cdn/hls-statuses` - Get paginated HLS conversion statuses
3. `GET /api/v1/cdn/status/{upload_id}` - Get specific upload status

## What Changed

### Previous Implementation
- Used **signed URLs** with query parameters (Policy, Signature, Key-Pair-Id)
- Each URL was individually signed
- URLs were long and contained sensitive parameters

### New Implementation
- Uses **signed cookies** for authorization
- Cookies are set once and authorize all CloudFront requests
- URLs are clean and unsigned (cookies handle authentication)
- Better security and user experience

## How It Works

### 1. Cookie Generation (`cdn_service.py`)

The `generate_cloudfront_signed_cookies()` function creates three CloudFront cookies:

```python
{
    "CloudFront-Policy": "<base64_encoded_policy>",
    "CloudFront-Signature": "<rsa_signature>",
    "CloudFront-Key-Pair-Id": "<your_key_pair_id>"
}
```

**Policy Structure:**
```json
{
    "Statement": [{
        "Resource": "https://your-cdn-domain.com/*",
        "Condition": {
            "DateLessThan": {
                "AWS:EpochTime": 1234567890
            }
        }
    }]
}
```

### 2. Cookie Setting (`cdn.py`)

The `set_signed_cookies()` helper function sets the cookies with:
- **Max Age**: 1 hour (configurable)
- **Path**: `/` (applies to all paths)
- **Domain**: Your CloudFront domain
- **Secure**: `true` (HTTPS only)
- **HttpOnly**: `true` (prevents JavaScript access)
- **SameSite**: `none` (allows cross-site requests)

### 3. Endpoint Flow

```
Client Request → Endpoint → Generate Cookies → Set in Response → Return Data
                                ↓
                    Client receives cookies automatically
                                ↓
                    Subsequent requests include cookies
                                ↓
                    CloudFront authorizes based on cookies
```

## Environment Variables Required

Ensure these are set in your `.env` file:

```env
CDN_KEY_GROUP_ID=YOUR_CLOUDFRONT_KEY_PAIR_ID
CDN_PRIVATE_KEY_PATH=./keys/private_key.pem
CDN_DOMAIN_NAME=your-cdn-domain.cloudfront.net
```

## CORS Configuration

The CORS middleware in `main.py` is already configured correctly:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://vamory.vadaevri.com", ...],
    allow_credentials=True,  # ✓ Required for cookies
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=3600,
)
```

## Frontend Integration

### React/JavaScript Example

```javascript
// 1. Fetch data from any of the three endpoints
// The cookies will be set automatically in the response
const response = await fetch('https://api.example.com/api/v1/cdn/urls?page=1&per_page=20', {
  method: 'GET',
  credentials: 'include',  // ✓ IMPORTANT: Include credentials to receive cookies
  headers: {
    'Content-Type': 'application/json',
    // Your authentication header if needed
  }
});

const data = await response.json();

// 2. Access the CDN URLs (unsigned URLs)
data.data.forEach(item => {
  const cdnUrl = item.cdn_url;  // https://your-cdn.cloudfront.net/path/to/file.mp4
  const m3u8Url = item.m3u8_url; // https://your-cdn.cloudfront.net/HLS_Converted/file.m3u8
  
  // 3. Use the URLs directly - cookies will authorize the requests
  // For HLS video player:
  const video = document.getElementById('video');
  if (Hls.isSupported()) {
    const hls = new Hls();
    hls.loadSource(m3u8Url);
    hls.attachMedia(video);
  }
});
```

### Important Frontend Notes

1. **Always use `credentials: 'include'`** in fetch requests
2. **Cookies are httpOnly** - you cannot access them via JavaScript
3. **Cookies expire after 1 hour** - refetch data to refresh cookies
4. **Cross-origin requests work** due to `SameSite=none` and `Secure=true`

## API Response Format

### GET /api/v1/cdn/urls

```json
{
  "data": [
    {
      "id": "uuid",
      "s3_key": "hls_source_video/file.mp4",
      "cdn_url": "https://your-cdn.cloudfront.net/hls_source_video/file.mp4",
      "m3u8_url": "https://your-cdn.cloudfront.net/HLS_Converted/file.m3u8",
      "created_at": "2024-01-01T00:00:00",
      "uploaded_at": "2024-01-01T00:01:00",
      "status": "COMPLETE"
    }
  ],
  "meta": {
    "total": 100,
    "page": 1,
    "per_page": 20,
    "total_pages": 5
  }
}
```

**Note**: URLs are now unsigned. The cookies in the response authorize access.

## Security Features

1. **RSA-SHA1 Signature**: CloudFront requirement for signed cookies
2. **HttpOnly Cookies**: Prevents XSS attacks
3. **Secure Flag**: Requires HTTPS in production
4. **SameSite=none**: Controlled cross-site access
5. **Time-Limited**: Cookies expire after 1 hour
6. **Domain-Scoped**: Only valid for your CloudFront domain

## Testing

### Test Cookie Setting

```bash
curl -X GET "http://localhost:8000/api/v1/cdn/urls?page=1&per_page=20" \
  -H "x-secret-key: YOUR_SECRET_KEY" \
  -v
```

Look for `Set-Cookie` headers in the response:
```
Set-Cookie: CloudFront-Policy=...; Domain=your-cdn.cloudfront.net; ...
Set-Cookie: CloudFront-Signature=...; Domain=your-cdn.cloudfront.net; ...
Set-Cookie: CloudFront-Key-Pair-Id=...; Domain=your-cdn.cloudfront.net; ...
```

### Test CDN Access

After receiving cookies, test accessing a CDN URL:
```bash
curl -X GET "https://your-cdn.cloudfront.net/path/to/file.mp4" \
  -H "Cookie: CloudFront-Policy=...; CloudFront-Signature=...; CloudFront-Key-Pair-Id=..." \
  -v
```

## Troubleshooting

### Cookies Not Being Set

1. **Check CORS**: Ensure `allow_credentials=True`
2. **Check Domain**: Cookie domain must match CloudFront domain
3. **Check HTTPS**: In production, cookies require HTTPS
4. **Check Browser**: Some browsers block third-party cookies

### CloudFront Returns 403 Forbidden

1. **Check Cookie Expiration**: Cookies expire after 1 hour
2. **Check Key Pair**: Ensure CloudFront distribution uses correct key group
3. **Check Private Key**: Verify private key path and permissions
4. **Check Policy**: Ensure policy includes the resource being accessed

### Frontend Not Receiving Cookies

1. **Use `credentials: 'include'`** in fetch/axios
2. **Check CORS**: Origin must be in allowed_origins list
3. **Check Browser Console**: Look for CORS errors
4. **Check Network Tab**: Verify Set-Cookie headers are present

## Differences from Example Code

The user provided example code using boto3's CloudFrontSigner. This implementation:

1. ✓ **Uses the same cryptographic approach** (RSA-SHA1)
2. ✓ **Generates the same cookie format** (Policy, Signature, Key-Pair-Id)
3. ✓ **Uses the same cookie attributes** (secure, httponly, samesite)
4. ✓ **Doesn't require boto3** (one less dependency)
5. ✓ **Async-compatible** (works with FastAPI)
6. ✓ **Integrated with existing structure** (uses your settings and services)

## Migration Notes

If you were using the old signed URL approach:

1. **URLs in database**: Old signed URLs will continue to work until expiration
2. **Frontend**: Update to use `credentials: 'include'` in requests
3. **Testing**: Test with a staging environment first
4. **Monitoring**: Monitor CloudFront logs for 403 errors during migration

## Benefits

1. **Cleaner URLs**: No long query parameters
2. **Better Security**: Cookies are httpOnly and secure
3. **Performance**: One cookie set authorizes all subsequent requests
4. **User Experience**: URLs can be shared without exposing signature
5. **Compliance**: Better control over content access

## Next Steps

1. **Test Locally**: Verify cookies are set correctly
2. **Update Frontend**: Add `credentials: 'include'` to fetch calls
3. **Deploy to Staging**: Test in a non-production environment
4. **Monitor Logs**: Check CloudFront and application logs
5. **Deploy to Production**: Roll out to production after testing

## Support

For issues or questions:
1. Check CloudFront distribution settings
2. Verify key pair is active in CloudFront
3. Review application logs for errors
4. Test cookie generation in isolation
5. Verify CORS configuration

---

**Implementation Date**: October 11, 2025
**Version**: 1.0.0
**Author**: AI Assistant

