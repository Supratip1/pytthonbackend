#!/usr/bin/env python3
"""
Test script to verify the AEO analysis integration
"""

import requests
import json
import time
from pathlib import Path

def test_api_server():
    """Test the API server endpoints"""
    base_url = "http://localhost:8000"
    
    print("🧪 Testing AEO Analysis API Server...")
    
    # Test health endpoint
    try:
        response = requests.get(f"{base_url}/health", timeout=5)
        if response.status_code == 200:
            print("✅ Health check passed")
        else:
            print(f"❌ Health check failed: {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"❌ Cannot connect to server: {e}")
        print("💡 Make sure the server is running with: python start_server.py")
        return False
    
    # Test analysis endpoint
    try:
        test_data = {
            "url": "https://healthline.com/",
            "max_pages": 3  # Small test to avoid long wait
        }
        
        print("🔄 Testing analysis endpoint...")
        response = requests.post(
            f"{base_url}/analyze",
            json=test_data,
            timeout=60  # Longer timeout for analysis
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get("success"):
                print("✅ Analysis completed successfully")
                print(f"📊 AEO Score: {result['data']['audit_report']['aeo_score_pct']}%")
                print(f"💡 Recommendations: {len(result['data']['optimization_recommendations']['optimizations'])} items")
                return True
            else:
                print(f"❌ Analysis failed: {result.get('error')}")
                return False
        else:
            print(f"❌ Analysis request failed: {response.status_code}")
            print(f"Response: {response.text}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Analysis request error: {e}")
        return False

def test_frontend_integration():
    """Test if frontend can connect to backend"""
    print("\n🌐 Testing Frontend Integration...")
    
    try:
        # Test if frontend development server is running
        response = requests.get("http://localhost:5173", timeout=5)
        if response.status_code == 200:
            print("✅ Frontend server is running")
        else:
            print(f"⚠️ Frontend server responded with: {response.status_code}")
    except requests.exceptions.RequestException:
        print("⚠️ Frontend server not running (expected if not started)")
    
    print("💡 To test full integration:")
    print("   1. Start backend: cd src/python && python start_server.py")
    print("   2. Start frontend: npm run dev")
    print("   3. Visit http://localhost:5173 and click 'Try Beta'")

def main():
    """Run all tests"""
    print("🚀 AEO Analysis Integration Test")
    print("=" * 40)
    
    # Test backend
    backend_ok = test_api_server()
    
    # Test frontend integration
    test_frontend_integration()
    
    print("\n" + "=" * 40)
    if backend_ok:
        print("🎉 Backend tests passed! Integration is working.")
    else:
        print("❌ Backend tests failed. Check the setup instructions.")
    
    print("\n📖 For complete setup instructions, see: src/python/README.md")

if __name__ == "__main__":
    main() 