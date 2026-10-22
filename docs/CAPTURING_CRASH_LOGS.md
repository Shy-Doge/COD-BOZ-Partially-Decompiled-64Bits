# Capturing a useful crash log

Android keeps only the last few minutes of the system log. If you collect it hours after a crash, the crash details are gone
(that is what happened with the first LAN crash reports). **Start recording before you reproduce the problem.**

1. Connect the phone with USB debugging on. Clear the old log: `adb logcat -b all -c`
2. Start recording into a file and leave the window open:
   ```
   adb logcat -b all -v threadtime > session.txt
   ```
3. On the phone, do the thing that crashes. When it crashed, press Ctrl+C in the terminal.
4. Send `session.txt` untouched. A native crash shows up in it as a block that starts with `*** *** ***` and the line
   `Fatal signal 11 (SIGSEGV)`, followed by the registers (`x0` ... `x30`, `sp`, `pc`) and a backtrace.
5. Also helpful: `adb shell getprop ro.build.version.release`, `adb shell getprop ro.product.model`.

Do not summarise the log; the exact lines are what matter.
