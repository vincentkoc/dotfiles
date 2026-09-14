#include <Network/Network.h>
#include <dispatch/dispatch.h>
#include <stdbool.h>
#include <stdio.h>

int main(void) {
    nw_path_monitor_t monitor = nw_path_monitor_create();
    if (monitor == NULL) {
        puts("unavailable");
        return 1;
    }
    dispatch_semaphore_t ready = dispatch_semaphore_create(0);
    dispatch_queue_t queue = dispatch_queue_create("low-data.network-cost", NULL);
    __block const char *cost = "unavailable";
    __block bool received = false;

    nw_path_monitor_set_update_handler(monitor, ^(nw_path_t path) {
        if (received) {
            return;
        }
        received = true;
        if (nw_path_get_status(path) != nw_path_status_satisfied) {
            cost = "unavailable";
        } else if (nw_path_is_constrained(path)) {
            cost = "constrained";
        } else if (nw_path_is_expensive(path)) {
            cost = "expensive";
        } else {
            cost = "unmetered";
        }
        dispatch_semaphore_signal(ready);
    });
    nw_path_monitor_set_queue(monitor, queue);
    nw_path_monitor_start(monitor);
    long timed_out = dispatch_semaphore_wait(
        ready, dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC));
    nw_path_monitor_cancel(monitor);
    if (timed_out != 0) {
        puts("unavailable");
        return 1;
    }
    puts(cost);
    return 0;
}
