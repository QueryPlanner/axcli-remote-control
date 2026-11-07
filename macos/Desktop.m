#import <ApplicationServices/ApplicationServices.h>
#import <Foundation/Foundation.h>
#import <ImageIO/ImageIO.h>
#import <ScreenCaptureKit/ScreenCaptureKit.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

enum {
    PermissionDenied = 10,
    DisplayUnavailable = 11,
    CaptureFailed = 12,
    CaptureTimeout = 13,
    InputFailed = 14,
};

static int fail(int code, NSString *message) {
    fprintf(stderr, "%s\n", message.UTF8String);
    return code;
}

static NSDictionary *describeDisplay(CGDirectDisplayID display) {
    CGRect bounds = CGDisplayBounds(display);
    return @{
        @"id": @(display), @"main": @(CGDisplayIsMain(display) ? YES : NO),
        @"x": @(bounds.origin.x), @"y": @(bounds.origin.y),
        @"width": @(bounds.size.width), @"height": @(bounds.size.height),
    };
}

static NSArray<NSDictionary *> *activeDisplays(void) {
    uint32_t count = 0;
    if (CGGetActiveDisplayList(0, NULL, &count) != kCGErrorSuccess || count == 0) return nil;
    NSMutableData *storage = [NSMutableData dataWithLength:count * sizeof(CGDirectDisplayID)];
    CGDirectDisplayID *ids = storage.mutableBytes;
    if (CGGetActiveDisplayList(count, ids, &count) != kCGErrorSuccess || count == 0) return nil;
    NSMutableArray *displays = [NSMutableArray arrayWithCapacity:count];
    for (uint32_t index = 0; index < count; index++) {
        [displays addObject:describeDisplay(ids[index])];
    }
    return displays;
}

static int writeJSON(id object) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:object options:0 error:NULL];
    if (!data) return fail(CaptureFailed, @"Could not encode display information");
    fwrite(data.bytes, 1, data.length, stdout);
    return 0;
}

static int checkScreenPermission(void) {
    if (CGPreflightScreenCaptureAccess()) return 0;
    return fail(PermissionDenied, @"Enable AXCLI Remote in Screen & System Audio Recording, then restart it");
}

static int captureDisplay(CGDirectDisplayID display, NSString *path) {
    int permission = checkScreenPermission();
    if (permission) return permission;
    NSDictionary *info = nil;
    for (NSDictionary *candidate in activeDisplays()) {
        if ([candidate[@"id"] unsignedIntValue] == display) {
            info = candidate;
            break;
        }
    }
    if (!info) {
        return fail(DisplayUnavailable, @"Display is unavailable. Check GET /v1/displays");
    }
    CGRect bounds = CGDisplayBounds(display);
    if (@available(macOS 15.2, *)) {
        dispatch_semaphore_t complete = dispatch_semaphore_create(0);
        __block NSString *failure = nil;
        [SCScreenshotManager captureImageInRect:bounds completionHandler:^(CGImageRef image, NSError *error) {
            if (!image) {
                failure = error.localizedDescription ?: @"macOS did not return a display image";
            } else {
                NSURL *url = [NSURL fileURLWithPath:path];
                CGImageDestinationRef output = CGImageDestinationCreateWithURL(
                    (__bridge CFURLRef)url, CFSTR("public.png"), 1, NULL);
                if (!output) {
                    failure = @"Could not open screenshot output";
                } else {
                    CGImageDestinationAddImage(output, image, NULL);
                    if (!CGImageDestinationFinalize(output)) failure = @"Could not write screenshot PNG";
                    CFRelease(output);
                }
            }
            dispatch_semaphore_signal(complete);
        }];
        if (dispatch_semaphore_wait(complete, dispatch_time(DISPATCH_TIME_NOW, 15 * NSEC_PER_SEC))) {
            return fail(CaptureTimeout, @"macOS timed out capturing the display");
        }
        if (failure) return fail(CaptureFailed, failure);
        return writeJSON(info);
    }
    return fail(CaptureFailed, @"Display screenshots require macOS 15.2 or later");
}

static int mouseGesture(BOOL drag, CGPoint start, CGPoint end) {
    if (!AXIsProcessTrusted() || !CGPreflightPostEventAccess()) {
        return fail(PermissionDenied, @"Enable AXCLI Remote in Device Control and Data Access");
    }
    CGMouseButton button = drag ? kCGMouseButtonLeft : kCGMouseButtonRight;
    CGEventRef down = CGEventCreateMouseEvent(NULL, drag ? kCGEventLeftMouseDown : kCGEventRightMouseDown, start, button);
    CGEventRef up = CGEventCreateMouseEvent(NULL, drag ? kCGEventLeftMouseUp : kCGEventRightMouseUp, end, button);
    CGEventRef move = drag ? CGEventCreateMouseEvent(NULL, kCGEventLeftMouseDragged, start, button) : NULL;
    if (!down || !up || (drag && !move)) {
        if (down) CFRelease(down);
        if (up) CFRelease(up);
        if (move) CFRelease(move);
        return fail(InputFailed, @"Could not create mouse events");
    }
    CGEventSetIntegerValueField(down, kCGMouseEventClickState, 1);
    CGEventSetIntegerValueField(up, kCGMouseEventClickState, 1);
    CGEventPost(kCGHIDEventTap, down);
    usleep(50000);
    if (move) {
        for (int step = 1; step <= 20; step++) {
            double fraction = step / 20.0;
            CGEventSetLocation(move, CGPointMake(
                start.x + (end.x - start.x) * fraction,
                start.y + (end.y - start.y) * fraction));
            CGEventPost(kCGHIDEventTap, move);
            usleep(12000);
        }
        CFRelease(move);
    }
    CGEventPost(kCGHIDEventTap, up);
    CFRelease(down);
    CFRelease(up);
    return 0;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc < 2) return EXIT_FAILURE;
        NSString *action = @(argv[1]);
        if (argc == 2 && [action isEqualToString:@"displays"]) {
            NSArray *displays = activeDisplays();
            return displays ? writeJSON(displays) : fail(DisplayUnavailable, @"No active Mac display");
        }
        if (argc == 2 && [action isEqualToString:@"check-screen"]) return checkScreenPermission();
        if (argc == 4 && [action isEqualToString:@"screenshot"]) {
            CGDirectDisplayID display = strcmp(argv[2], "main") == 0
                ? CGMainDisplayID() : (CGDirectDisplayID)strtoul(argv[2], NULL, 10);
            return captureDisplay(display, @(argv[3]));
        }
        if (argc == 4 && [action isEqualToString:@"right-click"]) {
            CGPoint point = CGPointMake(strtod(argv[2], NULL), strtod(argv[3], NULL));
            return mouseGesture(NO, point, point);
        }
        if (argc == 6 && [action isEqualToString:@"drag"]) {
            return mouseGesture(YES, CGPointMake(strtod(argv[2], NULL), strtod(argv[3], NULL)),
                CGPointMake(strtod(argv[4], NULL), strtod(argv[5], NULL)));
        }
        return EXIT_FAILURE;
    }
}
