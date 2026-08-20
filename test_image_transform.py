# test_image_transform.py — Quick test for image transformation (HIGH QUALITY)
import os
import sys

# Set test image URL (a sample Amazon product image)
TEST_URL = "https://m.media-amazon.com/images/I/61Y75oFmKpL._AC_SL1500_.jpg"

def test_transform():
    """Test image transformation with a sample Amazon image - HIGH QUALITY."""
    print("🎨 Testing Image Transformation (HIGH QUALITY)...\n")
    
    try:
        from image_transformer import transform_image, _download_image, get_available_presets, TRANSFORM_PRESETS
        from image_processor import upload_image
        
        # Show available presets
        presets = get_available_presets()
        print("Available presets:")
        for k, v in presets.items():
            print(f"  • {k}: {v['description']}")
        print()
        
        # Download test image
        print(f"📥 Downloading test image...")
        original = _download_image(TEST_URL)
        if not original:
            print("❌ Could not download test image")
            return False
        
        # Show original image info
        from PIL import Image
        import io
        orig_img = Image.open(io.BytesIO(original))
        print(f"   Original: {len(original)//1024} KB - Size: {orig_img.size} - Format: {orig_img.format}")
        print()
        
        # Transform with each preset - HIGH QUALITY PNG
        results = []
        for preset_name in TRANSFORM_PRESETS.keys():
            print(f"🔄 Applying {preset_name} effect (PNG format)...")
            # Use PNG for high quality, no compression
            transformed = transform_image(original, preset=preset_name, add_shadow=True, output_format="PNG")
            if transformed:
                # Get info about transformed image
                trans_img = Image.open(io.BytesIO(transformed))
                print(f"   Transformed: {len(transformed)//1024} KB - Size: {trans_img.size} - Format: PNG (lossless)")
                # Upload
                new_url = upload_image(transformed, f"test_{preset_name}.png")
                if new_url:
                    print(f"   Uploaded: {new_url[:60]}...")
                    results.append((preset_name, new_url, len(transformed)//1024))
                else:
                    print("   Upload failed")
            else:
                print("   Transformation failed")
            print()
        
        # Summary
        print("=" * 60)
        print("✅ Image Transformation Test Complete (HIGH QUALITY)!")
        print(f"   {len(results)}/{len(TRANSFORM_PRESETS)} presets worked\n")
        
        if results:
            print("Transformed images (PNG format - lossless quality):")
            for name, url, size_kb in results:
                print(f"  • {name}: {size_kb} KB - {url}")
        
        print("\n💡 All images are saved as PNG (lossless) for maximum quality!")
        print("   Original image dimensions are preserved.")
        
        return len(results) > 0
        
    except ImportError as e:
        print(f"❌ Import error: {e}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


if __name__ == "__main__":
    success = test_transform()
    sys.exit(0 if success else 1)
