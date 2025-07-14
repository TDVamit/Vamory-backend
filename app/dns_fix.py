"""
DNS Resolution Fix for MongoDB Atlas
Import this module before importing pymongo to fix DNS resolution issues.
"""

import os
import sys

def setup_dns_resolver():
    """Setup DNS resolver to use public DNS servers"""
    try:
        from dns import resolver
        
        # Create a custom resolver with public DNS servers
        r = resolver.Resolver(configure=False)  # Don't use system DNS settings
        r.nameservers = ['8.8.8.8', '1.1.1.1', '8.8.4.4', '1.0.0.1']  # Public DNS servers
        r.timeout = 10  # 10 second timeout
        r.lifetime = 30  # 30 second lifetime
        
        # Set as default resolver
        resolver.default_resolver = r
        
        print("SUCCESS: DNS resolver configured with public DNS servers")
        return True
        
    except ImportError:
        print("WARNING: dnspython not installed. Installing...")
        try:
            import subprocess
            subprocess.check_call([sys.executable, "-m", "pip", "install", "dnspython>=2.3.0"])
            print("SUCCESS: dnspython installed successfully")
            return setup_dns_resolver()  # Retry after installation
        except Exception as e:
            print(f"ERROR: Failed to install dnspython: {e}")
            return False
    except Exception as e:
        print(f"ERROR: Failed to setup DNS resolver: {e}")
        return False

def setup_environment_dns():
    """Setup environment variables for DNS resolution"""
    # Force pymongo to use dnspython for SRV resolution
    os.environ['PYMONGO_DNS_RESOLVER'] = 'dnspython'
    
    # Set custom DNS servers via environment
    os.environ['DNSPYTHON_NAMESERVERS'] = '8.8.8.8,1.1.1.1'
    
    print("SUCCESS: Environment variables set for DNS resolution")

# Auto-setup when module is imported
if __name__ != "__main__":
    setup_environment_dns()
    setup_dns_resolver()
