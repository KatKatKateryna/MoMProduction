"""
website_monitor.py
    -- Monitor website uptime and availability

This script checks if specified websites are up and running.
It can be used as a standalone script or integrated into the existing monitoring system.
"""

import configparser
import logging
import os
import smtplib
import ssl
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

# add path to access parent utilities
current = os.path.dirname(os.path.realpath(__file__))
parent = os.path.dirname(current)
sys.path.append(parent)


class WebsiteMonitor:
    """Monitor website availability and response time"""

    def __init__(
        self,
        config_file="website_monitor_config.cfg",
        monitor_config_file="monitor_config.cfg",
    ):
        self.config_file = config_file
        self.monitor_config_file = monitor_config_file
        self.config = configparser.ConfigParser()
        self.monitor_config = configparser.ConfigParser()
        self.websites = {}
        self.results = {}
        self.load_config()
        self.load_monitor_config()

    def load_config(self):
        """Load configuration from config file"""
        if os.path.exists(self.config_file):
            self.config.read(self.config_file)

            # Load websites from config
            if "WEBSITES" in self.config:
                self.websites = dict(self.config["WEBSITES"])
            else:
                # Default website if no config
                self.websites = {"eht_scigap": "https://eht.scigap.org"}
        else:
            # Create default config if it doesn't exist
            self.create_default_config()
            self.websites = {"eht_scigap": "https://eht.scigap.org"}

    def create_default_config(self):
        """Create a default configuration file"""
        self.config["WEBSITES"] = {
            "eht_scigap": "https://eht.scigap.org",
            # Add more websites as needed
            # 'example': 'https://example.com'
        }

        self.config["SETTINGS"] = {
            "timeout": "10",
            "retry_attempts": "3",
            "retry_delay": "5",
            "log_file": "website_monitor.log",
        }

        with open(self.config_file, "w") as configfile:
            self.config.write(configfile)

        print(f"Created default config file: {self.config_file}")

    def load_monitor_config(self):
        """Load monitor configuration for email settings"""
        if os.path.exists(self.monitor_config_file):
            self.monitor_config.read(self.monitor_config_file)
        else:
            print(
                f"Warning: Monitor config file {self.monitor_config_file} not found. Email notifications disabled."
            )

    def get_setting(self, key, default_value):
        """Get setting from config with default value"""
        try:
            return self.config.get("SETTINGS", key)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return default_value

    def check_website(self, name, url):
        """Check if a single website is accessible"""
        timeout = int(self.get_setting("timeout", "10"))
        retry_attempts = int(self.get_setting("retry_attempts", "3"))
        retry_delay = int(self.get_setting("retry_delay", "5"))

        for attempt in range(retry_attempts):
            try:
                start_time = time.time()
                response = requests.get(url, timeout=timeout)
                response_time = time.time() - start_time

                if response.status_code == 200:
                    return {
                        "status": "UP",
                        "status_code": response.status_code,
                        "response_time": round(response_time, 2),
                        "attempt": attempt + 1,
                        "timestamp": datetime.now(ZoneInfo("UTC")).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                    }
                else:
                    # Non-200 status code
                    if attempt == retry_attempts - 1:  # Last attempt
                        return {
                            "status": "DOWN",
                            "status_code": response.status_code,
                            "response_time": round(response_time, 2),
                            "attempt": attempt + 1,
                            "error": f"HTTP {response.status_code}",
                            "timestamp": datetime.now(ZoneInfo("UTC")).strftime(
                                "%Y-%m-%d %H:%M:%S"
                            ),
                        }

            except requests.exceptions.Timeout:
                if attempt == retry_attempts - 1:  # Last attempt
                    return {
                        "status": "DOWN",
                        "status_code": None,
                        "response_time": timeout,
                        "attempt": attempt + 1,
                        "error": "Timeout",
                        "timestamp": datetime.now(ZoneInfo("UTC")).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                    }

            except requests.exceptions.ConnectionError:
                if attempt == retry_attempts - 1:  # Last attempt
                    return {
                        "status": "DOWN",
                        "status_code": None,
                        "response_time": None,
                        "attempt": attempt + 1,
                        "error": "Connection Error",
                        "timestamp": datetime.now(ZoneInfo("UTC")).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                    }

            except requests.exceptions.RequestException as e:
                if attempt == retry_attempts - 1:  # Last attempt
                    return {
                        "status": "DOWN",
                        "status_code": None,
                        "response_time": None,
                        "attempt": attempt + 1,
                        "error": str(e),
                        "timestamp": datetime.now(ZoneInfo("UTC")).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                    }

            # Wait before retry (except for last attempt)
            if attempt < retry_attempts - 1:
                time.sleep(retry_delay)

        # This shouldn't be reached, but just in case
        return {
            "status": "UNKNOWN",
            "status_code": None,
            "response_time": None,
            "attempt": retry_attempts,
            "error": "Unknown error",
            "timestamp": datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%d %H:%M:%S"),
        }

    def check_all_websites(self):
        """Check all configured websites"""
        print(f"Checking {len(self.websites)} website(s)...")
        print("-" * 80)

        for name, url in self.websites.items():
            print(f"Checking {name}: {url}")
            result = self.check_website(name, url)
            self.results[name] = result

            # Print result
            status_color = "✅" if result["status"] == "UP" else "❌"
            print(f"{status_color} {name}: {result['status']}")

            if result["status"] == "UP":
                print(f"   Response time: {result['response_time']}s")
                print(f"   Status code: {result['status_code']}")
            else:
                print(f"   Error: {result.get('error', 'Unknown')}")
                if result.get("status_code"):
                    print(f"   Status code: {result['status_code']}")

            print(f"   Timestamp: {result['timestamp']}")
            print()

        return self.results

    def get_summary(self):
        """Get a summary of all website statuses"""
        if not self.results:
            return "No websites checked yet."

        total = len(self.results)
        up_count = sum(
            1 for result in self.results.values() if result["status"] == "UP"
        )
        down_count = total - up_count

        summary = f"""
Website Monitor Summary
======================
Total websites: {total}
Up: {up_count}
Down: {down_count}
Success rate: {(up_count / total) * 100:.1f}%
Checked at: {datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%d %H:%M:%S")}
"""
        return summary

    def log_results(self):
        """Log results to file"""
        log_file = self.get_setting("log_file", "website_monitor.log")

        # Setup logging
        logging.basicConfig(
            filename=log_file,
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        for name, result in self.results.items():
            url = self.websites[name]
            if result["status"] == "UP":
                logging.info(
                    f"{name} ({url}) is UP - Response time: {result['response_time']}s"
                )
            else:
                logging.warning(
                    f"{name} ({url}) is DOWN - Error: {result.get('error', 'Unknown')}"
                )

    def send_email_notification(self):
        """Send email notification with monitoring results"""
        try:
            # Check if Gmail config exists
            if "GMAIL" not in self.monitor_config:
                print("No Gmail configuration found. Email notification skipped.")
                return False

            gmail_config = self.monitor_config["GMAIL"]
            from_email = gmail_config.get("from_email")
            to_emails = gmail_config.get("to_emails", "").split(",")
            server = gmail_config.get("server", "smtp.gmail.com")
            port = int(gmail_config.get("port", "465"))
            password = gmail_config.get("password")

            if not all([from_email, to_emails, password]):
                print("Incomplete Gmail configuration. Email notification skipped.")
                return False

            # Check if any sites are down
            down_sites = [
                name
                for name, result in self.results.items()
                if result["status"] != "UP"
            ]

            # Create subject with warning if sites are down
            if down_sites:
                subject = (
                    f"⚠️ WARNING: Website Monitor Alert - {len(down_sites)} site(s) down"
                )
            else:
                subject = "✅ Website Monitor Report - All sites up"

            # Create email content
            body = self.create_email_body()

            # Create message
            msg = MIMEMultipart()
            msg["From"] = from_email
            msg["To"] = ", ".join(to_emails)
            msg["Subject"] = subject

            # Add body to email
            msg.attach(MIMEText(body, "plain"))

            # Create secure connection and send email
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(server, port, context=context) as smtp_server:
                smtp_server.login(from_email, password)
                smtp_server.sendmail(from_email, to_emails, msg.as_string())

            print(f"Email notification sent to: {', '.join(to_emails)}")
            return True

        except Exception as e:
            print(f"Failed to send email notification: {str(e)}")
            return False

    def create_email_body(self):
        """Create the email body with monitoring results"""
        if not self.results:
            return "No websites were checked."

        total = len(self.results)
        up_count = sum(
            1 for result in self.results.values() if result["status"] == "UP"
        )
        down_count = total - up_count

        body = f"""Website Monitoring Report
========================

Summary:
- Total websites monitored: {total}
- Sites UP: {up_count}
- Sites DOWN: {down_count}
- Success rate: {(up_count / total) * 100:.1f}%
- Report generated: {datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%d %H:%M:%S")}

Detailed Results:
"""

        for name, result in self.results.items():
            url = self.websites[name]
            status_icon = "✅" if result["status"] == "UP" else "❌"

            body += f"\n{status_icon} {name}: {result['status']}\n"
            body += f"   URL: {url}\n"
            body += f"   Timestamp: {result['timestamp']}\n"

            if result["status"] == "UP":
                body += f"   Response time: {result['response_time']}s\n"
                body += f"   Status code: {result['status_code']}\n"
            else:
                body += f"   Error: {result.get('error', 'Unknown')}\n"
                if result.get("status_code"):
                    body += f"   Status code: {result['status_code']}\n"
                body += f"   Attempts: {result.get('attempt', 'N/A')}\n"

            body += "\n"

        if down_count > 0:
            body += (
                "\n⚠️  ATTENTION REQUIRED: Some websites are not responding properly.\n"
            )
            body += "Please check the down sites and investigate any issues.\n"

        body += (
            f"\n---\nGenerated by Website Monitor\nConfiguration: {self.config_file}"
        )

        return body


def main():
    """Main function"""
    # Initialize monitor
    monitor = WebsiteMonitor()

    # Check all websites
    results = monitor.check_all_websites()

    # Print summary
    print(monitor.get_summary())

    # Log results
    monitor.log_results()

    # Send email notification
    monitor.send_email_notification()

    # Return non-zero exit code if any site is down
    down_sites = [name for name, result in results.items() if result["status"] != "UP"]
    if down_sites:
        print(f"Warning: {len(down_sites)} site(s) are down: {', '.join(down_sites)}")
        sys.exit(1)
    else:
        print("All websites are up!")
        sys.exit(0)


if __name__ == "__main__":
    main()
