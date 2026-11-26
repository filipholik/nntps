CLANG ?= clang

all: counter.o

counter.o: counter.c
	$(CLANG) -O2 -g -target bpf -c $< -o $@

clean:
	rm -f counter.o
