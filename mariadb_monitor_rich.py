#!/usr/bin/env python3
"""
MariaDB Connection Monitor using curses
Alternative version for systems without Rich library
"""

import curses
import time
import sys
import os
from datetime import datetime
from collections import deque
import signal
import subprocess # Added for php-fpm stats

try:
    import mysql.connector
except ImportError:
    print("Installing mysql-connector-python...")
    os.system("pip3 install mysql-connector-python")
    import mysql.connector

# Database configuration
DB_CONFIG = {
    'host': 'localhost',
    'user': 'mreport',
    'password': 'M@1234',
    'database': 'mreport'
}

class MariaDBCursesMonitor:
    def __init__(self):
        self.connection_history = deque(maxlen=30)
        self.max_connections = 0
        self.max_php_fpm_connections = 150  # Default for pm.max_children, adjust if needed
        self.is_running = True
        self.colors = {}

    def init_colors(self):
        """Initialize color pairs for curses"""
        curses.start_color()
        curses.init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK)    # Green
        curses.init_pair(2, curses.COLOR_YELLOW, curses.COLOR_BLACK)   # Yellow
        curses.init_pair(3, curses.COLOR_RED, curses.COLOR_BLACK)      # Red
        curses.init_pair(4, curses.COLOR_CYAN, curses.COLOR_BLACK)     # Cyan
        curses.init_pair(5, curses.COLOR_MAGENTA, curses.COLOR_BLACK)  # Magenta
        curses.init_pair(6, curses.COLOR_WHITE, curses.COLOR_BLACK)    # White
        curses.init_pair(7, curses.COLOR_BLUE, curses.COLOR_BLACK)     # Blue

        self.colors = {
            'green': curses.color_pair(1),
            'yellow': curses.color_pair(2),
            'red': curses.color_pair(3),
            'cyan': curses.color_pair(4),
            'magenta': curses.color_pair(5),
            'white': curses.color_pair(6),
            'blue': curses.color_pair(7)
        }

    def get_db_connection(self):
        """Get database connection"""
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            return conn
        except mysql.connector.Error:
            return None

    def get_php_fpm_connections(self):
        """Get current php-fpm connection count using shell command"""
        try:
            # The command counts lines from netstat output that contain 'php-fpm'
            # 2>/dev/null redirects stderr to avoid printing errors if netstat is not available
            command = "netstat -tapn 2>/dev/null | grep php-fpm | wc -l"
            result = subprocess.check_output(command, shell=True, text=True)
            return int(result.strip())
        except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
            # command fails, or netstat not found, or output is not a number
            return -1 # Indicate error

    def get_stats(self): # Renamed from get_connection_stats
        """Get current connection statistics for MariaDB and PHP-FPM"""
        conn = self.get_db_connection()
        
        stats = {}

        if not conn:
            # Still return php-fpm stats even if db connection fails
            php_fpm_stats = self.get_php_fpm_connections()
            if php_fpm_stats != -1:
                stats['php_fpm_connections'] = php_fpm_stats
            return stats if stats else None

        try:
            cursor = conn.cursor(dictionary=True)

            # Get connection statistics
            cursor.execute("SHOW VARIABLES LIKE 'max_connections'")
            max_conn = cursor.fetchone()
            self.max_connections = int(max_conn['Value'])

            cursor.execute("SHOW STATUS LIKE 'Threads_connected'")
            current_conn = cursor.fetchone()
            current_connections = int(current_conn['Value'])

            cursor.execute("SHOW STATUS LIKE 'Threads_running'")
            running_threads = cursor.fetchone()
            threads_running = int(running_threads['Value'])

            cursor.execute("SHOW STATUS LIKE 'Max_used_connections'")
            max_used = cursor.fetchone()
            max_used_connections = int(max_used['Value'])

            cursor.execute("SHOW STATUS LIKE 'Aborted_connects'")
            aborted = cursor.fetchone()
            aborted_connects = int(aborted['Value'])

            # Get active processes
            cursor.execute("""
                SELECT User, Host, db, Command, Time, State
                FROM INFORMATION_SCHEMA.PROCESSLIST
                WHERE Command != 'Sleep'
                ORDER BY Time DESC
                LIMIT 8
            """)
            active_processes = cursor.fetchall()

            # Get connection count by user
            cursor.execute("""
                SELECT User, COUNT(*) as count
                FROM INFORMATION_SCHEMA.PROCESSLIST
                GROUP BY User
                ORDER BY count DESC
                LIMIT 5
            """)
            user_connections = cursor.fetchall()

            db_stats = {
                'max_connections': self.max_connections,
                'current_connections': current_connections,
                'threads_running': threads_running,
                'max_used_connections': max_used_connections,
                'aborted_connects': aborted_connects,
                'active_processes': active_processes,
                'user_connections': user_connections,
                'usage_percentage': (current_connections / self.max_connections) * 100 if self.max_connections > 0 else 0,
                'timestamp': datetime.now()
            }
            stats.update(db_stats)

            # Store in history
            self.connection_history.append({
                'timestamp': datetime.now(),
                'connections': current_connections,
                'usage': stats['usage_percentage']
            })

        except mysql.connector.Error:
            pass # Silently fail on db error but continue to fetch php-fpm
        finally:
            if conn:
                conn.close()
        
        # Get PHP-FPM stats
        php_fpm_stats = self.get_php_fpm_connections()
        if php_fpm_stats != -1:
            stats['php_fpm_connections'] = php_fpm_stats

        return stats if stats else None

    def draw_progress_bar(self, stdscr, y, x, width, current, maximum, label):
        """Draw a progress bar with enhanced phase indicators"""
        if maximum == 0:
            percentage = 0
        else:
            percentage = (current / maximum) * 100

        # Enhanced phase-based color and status
        if percentage < 30:
            color = self.colors['green']
            status = "🟢 ปลอดภัย"
            status_en = "SAFE"
        elif percentage < 50:
            color = self.colors['green']
            status = "🟢 ดี"
            status_en = "GOOD"
        elif percentage < 70:
            color = self.colors['yellow']
            status = "🟡 ปานกลาง"
            status_en = "MODERATE"
        elif percentage < 85:
            color = self.colors['yellow']
            status = "🟠 สูง"
            status_en = "HIGH"
        elif percentage < 95:
            color = self.colors['red']
            status = "🔴 วิกฤต"
            status_en = "CRITICAL"
        else:
            color = self.colors['red']
            status = "🚨 อันตราย!"
            status_en = "DANGER"

        # Draw label with status
        stdscr.addstr(y, x, f"{label}:", self.colors['cyan'])
        stdscr.addstr(y, x + 25, f"[{status_en}]", color | curses.A_BOLD)

        # Calculate filled portion
        filled_width = int((width * current) / maximum) if maximum > 0 else 0

        # Draw progress bar
        bar_y = y + 1
        stdscr.addch(bar_y, x, '[', self.colors['white'])

        for i in range(width):
            if i < filled_width:
                stdscr.addch(bar_y, x + 1 + i, '█', color | curses.A_BOLD)
            else:
                stdscr.addch(bar_y, x + 1 + i, '░', self.colors['white'])

        stdscr.addch(bar_y, x + width + 1, ']', self.colors['white'])

        # Add percentage, values and status
        info_text = f" {percentage:5.1f}% ({current:,}/{maximum:,})"
        stdscr.addstr(bar_y, x + width + 3, info_text, color)

        # Add Thai status on next line
        try:
            stdscr.addstr(bar_y + 1, x + 2, status, color | curses.A_BOLD)
        except curses.error:
            pass

        return bar_y + 3

    def draw_stats_table(self, stdscr, start_y, start_x, stats):
        """Draw statistics table with enhanced phase indicators"""
        stdscr.addstr(start_y, start_x, "📊 MariaDB Statistics",
                     curses.A_BOLD | self.colors['cyan'])
        stdscr.addstr(start_y + 1, start_x, "─" * 60, self.colors['white'])

        y = start_y + 2

        if 'current_connections' not in stats:
            stdscr.addstr(y, start_x, "MariaDB data not available.", self.colors['yellow'])
            return y + 2

        # Enhanced current connections status
        usage_pct = stats['usage_percentage']
        if usage_pct < 30:
            current_color = self.colors['green']
            current_status = "🟢 ปลอดภัย"
        elif usage_pct < 50:
            current_color = self.colors['green']
            current_status = "🟢 ดี"
        elif usage_pct < 70:
            current_color = self.colors['yellow']
            current_status = "🟡 ปานกลาง"
        elif usage_pct < 85:
            current_color = self.colors['yellow']
            current_status = "🟠 สูง"
        elif usage_pct < 95:
            current_color = self.colors['red']
            current_status = "🔴 วิกฤต"
        else:
            current_color = self.colors['red']
            current_status = "🚨 อันตราย"

        stdscr.addstr(y, start_x, f"Current Connections: ", self.colors['white'])
        stdscr.addstr(y, start_x + 20, f"{stats['current_connections']:,}", current_color | curses.A_BOLD)
        stdscr.addstr(y, start_x + 30, f" {current_status}", current_color)
        y += 1

        # Max connections
        stdscr.addstr(y, start_x, f"Max Connections:     ", self.colors['white'])
        stdscr.addstr(y, start_x + 20, f"{stats['max_connections']:,}", self.colors['blue'] | curses.A_BOLD)
        stdscr.addstr(y, start_x + 30, f" 📋 Config", self.colors['blue'])
        y += 1

        # Enhanced running threads status
        threads_ratio = (stats['threads_running'] / max(stats['current_connections'], 1)) * 100 if stats['current_connections'] > 0 else 0
        if threads_ratio < 30:
            running_color = self.colors['green']
            running_status = "🟢 เบา"
        elif threads_ratio < 60:
            running_color = self.colors['yellow']
            running_status = "🟡 ปกติ"
        else:
            running_color = self.colors['red']
            running_status = "🔴 หนัก"

        stdscr.addstr(y, start_x, f"Running Threads:     ", self.colors['white'])
        stdscr.addstr(y, start_x + 20, f"{stats['threads_running']:,}", running_color | curses.A_BOLD)
        stdscr.addstr(y, start_x + 30, f" {running_status}", running_color)
        y += 1

        # Max used with status
        max_used_ratio = (stats['max_used_connections'] / stats['max_connections']) * 100 if stats['max_connections'] > 0 else 0
        if max_used_ratio < 50:
            max_used_color = self.colors['green']
            max_used_status = "🟢 ต่ำ"
        elif max_used_ratio < 80:
            max_used_color = self.colors['yellow']
            max_used_status = "🟡 ปกติ"
        else:
            max_used_color = self.colors['red']
            max_used_status = "🔴 สูง"

        stdscr.addstr(y, start_x, f"Max Used Ever:       ", self.colors['white'])
        stdscr.addstr(y, start_x + 20, f"{stats['max_used_connections']:,}", max_used_color | curses.A_BOLD)
        stdscr.addstr(y, start_x + 30, f" {max_used_status}", max_used_color)
        y += 1

        # Enhanced aborted connections status
        if stats['aborted_connects'] == 0:
            abort_color = self.colors['green']
            abort_status = "🟢 เยี่ยม"
        elif stats['aborted_connects'] < 10:
            abort_color = self.colors['yellow']
            abort_status = "🟡 ปกติ"
        else:
            abort_color = self.colors['red']
            abort_status = "🔴 ผิดปกติ"

        stdscr.addstr(y, start_x, f"Aborted Connects:    ", self.colors['white'])
        stdscr.addstr(y, start_x + 20, f"{stats['aborted_connects']:,}", abort_color | curses.A_BOLD)
        stdscr.addstr(y, start_x + 30, f" {abort_status}", abort_color)

        return y + 2

    def draw_process_table(self, stdscr, start_y, start_x, processes):
        """Draw active processes table"""
        stdscr.addstr(start_y, start_x, "🔄 Active Processes",
                     curses.A_BOLD | self.colors['cyan'])
        stdscr.addstr(start_y + 1, start_x, "─" * 60, self.colors['white'])

        # Header
        y = start_y + 2
        stdscr.addstr(y, start_x, "User".ljust(12), self.colors['cyan'])
        stdscr.addstr(y, start_x + 12, "Host".ljust(18), self.colors['cyan'])
        stdscr.addstr(y, start_x + 30, "DB".ljust(10), self.colors['cyan'])
        stdscr.addstr(y, start_x + 40, "Cmd".ljust(10), self.colors['cyan'])
        stdscr.addstr(y, start_x + 50, "Time", self.colors['cyan'])
        y += 1

        if not processes:
            stdscr.addstr(y, start_x, "No active processes or data unavailable", self.colors['white'])
        else:
            for proc in processes[:6]:  # Show max 6 processes
                time_color = self.colors['green'] if proc['Time'] < 10 else self.colors['yellow'] if proc['Time'] < 60 else self.colors['red']

                stdscr.addstr(y, start_x, (proc['User'] or 'N/A')[:11].ljust(12), self.colors['white'])
                stdscr.addstr(y, start_x + 12, (proc['Host'] or 'N/A')[:17].ljust(18), self.colors['green'])
                stdscr.addstr(y, start_x + 30, (proc['db'] or 'N/A')[:9].ljust(10), self.colors['blue'])
                stdscr.addstr(y, start_x + 40, (proc['Command'] or 'N/A')[:9].ljust(10), self.colors['yellow'])
                stdscr.addstr(y, start_x + 50, f"{proc['Time']}s", time_color)
                y += 1

        return y + 1

    def draw_user_connections(self, stdscr, start_y, start_x, user_connections):
        """Draw user connections table"""
        stdscr.addstr(start_y, start_x, "👥 Connections by User",
                     curses.A_BOLD | self.colors['cyan'])
        stdscr.addstr(start_y + 1, start_x, "─" * 30, self.colors['white'])

        y = start_y + 2
        if not user_connections:
            stdscr.addstr(y, start_x, "Data unavailable", self.colors['white'])
        else:
            for user_conn in user_connections:
                count_color = self.colors['green'] if user_conn['count'] < 10 else self.colors['yellow'] if user_conn['count'] < 50 else self.colors['red']

                stdscr.addstr(y, start_x, f"{(user_conn['User'] or 'N/A')}:", self.colors['white'])
                stdscr.addstr(y, start_x + 15, f"{user_conn['count']}", count_color)
                y += 1

        return y + 1

    def draw_history_chart(self, stdscr, start_y, start_x):
        """Draw connection history chart"""
        stdscr.addstr(start_y, start_x, "📈 Connection History",
                     curses.A_BOLD | self.colors['cyan'])
        stdscr.addstr(start_y + 1, start_x, "─" * 40, self.colors['white'])

        if len(self.connection_history) < 2:
            stdscr.addstr(start_y + 2, start_x, "Loading history...", self.colors['white'])
            return start_y + 4

        y = start_y + 2
        recent_history = list(self.connection_history)[-10:]  # Last 10 readings

        max_val = max(h['connections'] for h in recent_history) if recent_history else 0
        max_scale = max(max_val, 1)

        for entry in recent_history:
            time_str = entry['timestamp'].strftime("%H:%M:%S")
            connections = entry['connections']
            usage = entry['usage']

            # Create simple bar
            bar_length = int((connections / max_scale) * 20)
            bar_color = self.colors['green'] if usage < 50 else self.colors['yellow'] if usage < 80 else self.colors['red']

            stdscr.addstr(y, start_x, f"{time_str} ", self.colors['white'])

            # Draw bar
            for i in range(20):
                if i < bar_length:
                    stdscr.addch(y, start_x + 9 + i, '█', bar_color)
                else:
                    stdscr.addch(y, start_x + 9 + i, '░', self.colors['white'])

            stdscr.addstr(y, start_x + 30, f" {connections:3d} ({usage:4.1f}%)", self.colors['white'])
            y += 1

        return y + 1

    def draw_dashboard(self, stdscr, stats):
        """Draw the main dashboard"""
        stdscr.clear()
        height, width = stdscr.getmaxyx()

        # Header
        header = "🗄️ MariaDB & PHP-FPM Real-time Monitor"
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if not stats:
            # Connection error message
            stdscr.addstr(height//2, width//2 - 15, "❌ Cannot fetch any data",
                         curses.A_BOLD | self.colors['red'])
            stdscr.addstr(height//2 + 1, width//2 - 20, "Check services and permissions",
                         self.colors['white'])
            stdscr.refresh()
            return

        # Draw header
        stdscr.addstr(0, (width - len(header)) // 2, header,
                     curses.A_BOLD | self.colors['green'])
        stdscr.addstr(1, (width - len(current_time)) // 2, current_time, self.colors['white'])
        stdscr.addstr(2, 0, "─" * width, self.colors['white'])

        # Draw progress bars
        y_pos = 4
        bar_width = 40

        if 'current_connections' in stats:
            y_pos = self.draw_progress_bar(stdscr, y_pos, 5, bar_width,
                                         stats['current_connections'],
                                         stats['max_connections'],
                                         "MariaDB Connections")

        if 'threads_running' in stats:
            y_pos = self.draw_program_bar(stdscr, y_pos, 5, bar_width,
                                         stats['threads_running'],
                                         max(stats.get('current_connections', 1), 1),
                                         "Running Threads")

        if stats.get('php_fpm_connections', -1) != -1:
            y_pos = self.draw_progress_bar(stdscr, y_pos, 5, bar_width,
                                         stats['php_fpm_connections'],
                                         self.max_php_fpm_connections,
                                         "PHP-FPM Connections")

        # Split screen into columns
        left_col = 2
        right_col = width // 2 + 2
        col_y_start = y_pos + 1

        # Left column - Statistics and Users
        y_pos_left = self.draw_stats_table(stdscr, col_y_start, left_col, stats)
        self.draw_user_connections(stdscr, y_pos_left + 1, left_col, stats.get('user_connections'))

        # Right column - Processes and History
        y_pos_right = self.draw_process_table(stdscr, col_y_start, right_col, stats.get('active_processes'))
        self.draw_history_chart(stdscr, y_pos_right + 1, right_col)

        # Footer
        footer = "Press 'q' to quit | Updates every 5 seconds"
        try:
            stdscr.addstr(height - 2, (width - len(footer)) // 2, footer,
                         curses.A_DIM | self.colors['white'])
        except curses.error:
            pass  # Ignore if we can't draw at the bottom

        stdscr.refresh()

    def run_curses(self, stdscr):
        """Run the curses interface"""
        self.init_colors()
        stdscr.nodelay(True)  # Non-blocking input
        stdscr.timeout(100)   # Timeout for getch()
        curses.curs_set(0)    # Hide cursor

        while self.is_running:
            try:
                # Get input
                key = stdscr.getch()
                if key == ord('q') or key == ord('Q'):
                    break

                # Get stats and draw
                stats = self.get_stats() # Use renamed function
                self.draw_dashboard(stdscr, stats)

                # Wait 5 seconds (50 * 0.1)
                for _ in range(50):
                    key = stdscr.getch()
                    if key == ord('q') or key == ord('Q'):
                        self.is_running = False
                        break
                    time.sleep(0.1)

            except KeyboardInterrupt:
                break
            except Exception as e:
                # Error handling
                height, width = stdscr.getmaxyx()
                stdscr.clear()
                error_message = f"Error: {str(e)}"
                stdscr.addstr(height//2, max(0, width//2 - len(error_message)//2), error_message,
                             curses.A_BOLD | self.colors['red'])
                stdscr.refresh()
                time.sleep(2)

    def run(self):
        """Run the monitor"""
        try:
            curses.wrapper(self.run_curses)
        except Exception as e:
            print(f"Error starting monitor: {e}")
            return 1
        return 0

def main():
    """Main function"""
    print("🚀 Starting MariaDB & PHP-FPM Curses Monitor...")
    print("Press 'q' to quit")

    monitor = MariaDBCursesMonitor()
    exit_code = monitor.run()

    print("
👋 Monitor stopped. Goodbye!")
    return exit_code

if __name__ == "__main__":
    sys.exit(main())