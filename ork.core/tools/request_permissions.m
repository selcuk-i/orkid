// request_permissions.m
//
// Tiny ObjC helper shipped inside every orkid-based gui-mode .app bundle.
// Called once during downloader install (or on first launch as a fallback)
// to request macOS Camera and Microphone TCC grants up front, while the
// Mac display is still visible — BEFORE any VR/HMD takeover in the actual
// app would hide a later prompt.
//
// TCC attribution is per-bundle-id. Because this Mach-O lives inside the
// target .app and is signed with that .app's CFBundleIdentifier, the
// system prompt is attributed to the target app itself. Granting here
// carries over to the real app's subprocess chain (which inherits via
// responsible_pid, same mechanism obt_app_launcher relies on).
//
// Runs synchronously, waits for the user's response to both prompts,
// then exits. Exit code 0 always — partial denials are not treated as
// errors because the app still runs (just without those permissions).

#import <AVFoundation/AVFoundation.h>
#import <Foundation/Foundation.h>
#import <stdio.h>

int main(int argc, char **argv) {
  @autoreleasepool {
    dispatch_semaphore_t sem = dispatch_semaphore_create(0);
    __block int pending = 2;

    void (^completion)(BOOL) = ^(BOOL granted) {
      // Use atomic decrement via semaphore-gated counter. Since both
      // callbacks may run on different queues, we protect `pending`
      // with a plain barrier by signaling only when pending reaches 0
      // under a mutex-equivalent (serial dispatch via the semaphore).
      @synchronized([NSProcessInfo processInfo]) {
        pending--;
        if (pending == 0) {
          dispatch_semaphore_signal(sem);
        }
      }
    };

    fprintf(stdout, "[request_permissions] requesting Camera...\n");
    [AVCaptureDevice requestAccessForMediaType:AVMediaTypeVideo
                             completionHandler:^(BOOL granted) {
      fprintf(stdout, "[request_permissions] Camera: %s\n",
              granted ? "granted" : "denied");
      completion(granted);
    }];

    fprintf(stdout, "[request_permissions] requesting Microphone...\n");
    [AVCaptureDevice requestAccessForMediaType:AVMediaTypeAudio
                             completionHandler:^(BOOL granted) {
      fprintf(stdout, "[request_permissions] Microphone: %s\n",
              granted ? "granted" : "denied");
      completion(granted);
    }];

    // Wait up to 5 minutes (user might take a while to click).
    dispatch_time_t deadline = dispatch_time(
      DISPATCH_TIME_NOW, 5LL * 60LL * NSEC_PER_SEC);
    long timed_out = dispatch_semaphore_wait(sem, deadline);
    if (timed_out != 0) {
      fprintf(stderr, "[request_permissions] timed out waiting for user\n");
      return 0;
    }
    fprintf(stdout, "[request_permissions] done\n");
  }
  return 0;
}
