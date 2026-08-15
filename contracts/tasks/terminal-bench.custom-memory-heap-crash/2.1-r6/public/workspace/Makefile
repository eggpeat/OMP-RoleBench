CXX ?= g++
CXXFLAGS ?= -std=c++17

DEBUG_LIB ?= /usr/local/gcc-custom-debug/lib64
RELEASE_LIB ?= /usr/local/gcc-custom-release/lib64

all: release debug

release: main.cpp user.cpp user.h
	@if [ -f "$(RELEASE_LIB)/libstdc++.a" ]; then \
		$(CXX) $(CXXFLAGS) -o release main.cpp user.cpp -O2 -DNDEBUG "$(RELEASE_LIB)/libstdc++.a" -lm; \
	else \
		echo "Error: Required custom release runtime archive $(RELEASE_LIB)/libstdc++.a not found." >&2; \
		exit 1; \
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
