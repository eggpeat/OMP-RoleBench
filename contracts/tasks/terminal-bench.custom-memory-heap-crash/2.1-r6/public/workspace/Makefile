CXX ?= g++
CXXFLAGS ?= -std=c++17

DEBUG_LIB ?= /usr/local/gcc-custom-debug/lib64
RELEASE_LIB ?= /usr/local/gcc-custom-release/lib64

all: release debug

release: main.cpp user.cpp user.h
	@if [ -d "$(RELEASE_LIB)" ]; then \
		$(CXX) $(CXXFLAGS) -o release main.cpp user.cpp -O2 -DNDEBUG -L$(RELEASE_LIB) -Wl,-Bstatic -lstdc++ -Wl,-Bdynamic -lm; \
	else \
		$(CXX) $(CXXFLAGS) -o release main.cpp user.cpp -O2 -DNDEBUG -lm; \
	fi

debug: main.cpp user.cpp user.h
	@if [ -d "$(DEBUG_LIB)" ]; then \
		$(CXX) $(CXXFLAGS) -o debug main.cpp user.cpp -g -O0 -L$(DEBUG_LIB) -Wl,-Bstatic -lstdc++ -Wl,-Bdynamic -lm; \
	else \
		$(CXX) $(CXXFLAGS) -o debug main.cpp user.cpp -g -O0 -lm; \
	fi

clean:
	rm -f release debug

.PHONY: all release debug clean
