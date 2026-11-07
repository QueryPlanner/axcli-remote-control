#import <Cocoa/Cocoa.h>
#include <fcntl.h>
#include <signal.h>
#include <unistd.h>

static volatile sig_atomic_t childPID = 0;

static void forwardSignal(int signalNumber) {
    if (childPID > 0) kill(childPID, signalNumber);
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSApplication *application = [NSApplication sharedApplication];
        [application setActivationPolicy:NSApplicationActivationPolicyAccessory];

        NSString *resources = [[NSBundle mainBundle] resourcePath];
        NSString *stateDirectory = [NSHomeDirectory() stringByAppendingPathComponent:
            @"Library/Application Support/AXCLI Remote"];
        [[NSFileManager defaultManager] createDirectoryAtPath:stateDirectory
            withIntermediateDirectories:YES attributes:@{NSFilePosixPermissions: @0700}
            error:nil];

        NSString *logPath = [stateDirectory stringByAppendingPathComponent:@"server.log"];
        int logFD = open([logPath fileSystemRepresentation], O_WRONLY | O_CREAT | O_APPEND, 0600);
        if (logFD < 0) return 1;
        dup2(logFD, STDOUT_FILENO);
        dup2(logFD, STDERR_FILENO);
        close(logFD);

        NSString *python = [[NSString stringWithContentsOfFile:
            [resources stringByAppendingPathComponent:@"python-executable.txt"]
            encoding:NSUTF8StringEncoding error:nil]
            stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
        if (!python.length) return 1;

        NSTask *task = [[NSTask alloc] init];
        task.executableURL = [NSURL fileURLWithPath:python];
        task.arguments = @[@"-s", [resources stringByAppendingPathComponent:@"bootstrap.py"]];
        task.currentDirectoryURL = [NSURL fileURLWithPath:stateDirectory];
        NSMutableDictionary *environment = [[[NSProcessInfo processInfo] environment] mutableCopy];
        environment[@"PYTHONPATH"] = [resources stringByAppendingPathComponent:@"python"];
        environment[@"PYTHONNOUSERSITE"] = @"1";
        environment[@"PYTHONUNBUFFERED"] = @"1";
        environment[@"PYTHONDONTWRITEBYTECODE"] = @"1";
        environment[@"AXCLI_BIN"] = [resources stringByAppendingPathComponent:@"axcli"];
        environment[@"AXCLI_TOKEN_FILE"] = [stateDirectory stringByAppendingPathComponent:@"api_token"];
        [environment removeObjectForKey:@"AXCLI_API_TOKEN"];
        [environment removeObjectForKey:@"AXCLI_BIND_HOST"];
        task.environment = environment;
        task.standardOutput = [NSFileHandle fileHandleWithStandardOutput];
        task.standardError = [NSFileHandle fileHandleWithStandardError];
        task.terminationHandler = ^(NSTask *finishedTask) {
            dispatch_async(dispatch_get_main_queue(), ^{ [application terminate:nil]; });
        };

        NSError *error = nil;
        if (![task launchAndReturnError:&error]) {
            NSLog(@"Could not launch AXCLI API: %@", error);
            return 1;
        }
        childPID = task.processIdentifier;
        signal(SIGTERM, forwardSignal);
        signal(SIGINT, forwardSignal);
        [application run];
        if (task.running) [task terminate];
    }
    return 0;
}
