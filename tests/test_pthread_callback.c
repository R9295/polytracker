#include <fcntl.h>
#include <pthread.h>
#include <unistd.h>

static void *copy_input(void *path) {
  int fd = open((const char *)path, O_RDONLY);
  char byte;
  if (fd < 0 || read(fd, &byte, 1) != 1)
    return (void *)1;
  close(fd);
  if (write(STDOUT_FILENO, &byte, 1) != 1)
    return (void *)1;
  return 0;
}

int main(int argc, char **argv) {
  pthread_t thread;
  void *result;
  if (argc != 2 || pthread_create(&thread, 0, copy_input, argv[1]) != 0)
    return 1;
  if (pthread_join(thread, &result) != 0)
    return 1;
  return result != 0;
}
