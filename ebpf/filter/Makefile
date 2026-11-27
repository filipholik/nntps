CLANG ?= clang

all: filter.o

%.o: %.c
	$(CLANG) -O2 -g -target bpf -c $< -o $@

clean:
	rm -f *.o
