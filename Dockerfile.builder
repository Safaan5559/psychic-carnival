FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive ANDROID_HOME=/opt/android-sdk ANDROID_SDK_ROOT=/opt/android-sdk JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 PATH=/opt/android-sdk/cmdline-tools/latest/bin:/opt/android-sdk/platform-tools:/opt/android-sdk/build-tools/35.0.0:$PATH
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip python3-venv openjdk-17-jdk git unzip zip wget curl build-essential autoconf automake autopoint ccache cmake gettext libffi-dev libltdl-dev libssl-dev libtool pkg-config patch lbzip2 && rm -rf /var/lib/apt/lists/*
RUN mkdir -p ${ANDROID_HOME}/cmdline-tools && wget -q https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip -O /tmp/sdk.zip && unzip -q /tmp/sdk.zip -d ${ANDROID_HOME}/cmdline-tools && mv ${ANDROID_HOME}/cmdline-tools/cmdline-tools ${ANDROID_HOME}/cmdline-tools/latest && rm /tmp/sdk.zip && yes | sdkmanager --licenses >/dev/null || true && sdkmanager "platform-tools" "platforms;android-36" "build-tools;35.0.0" "ndk;28.2.13676358"
RUN python3 -m pip install --break-system-packages --no-cache-dir "buildozer==1.5.0"
RUN useradd -m -u 10001 builder && mkdir -p /home/builder/.buildozer /workspace/app /workspace/out && chown -R builder:builder /home/builder /workspace
COPY docker/entrypoint.sh /usr/local/bin/py2apk-entrypoint
RUN chmod 755 /usr/local/bin/py2apk-entrypoint
USER builder
WORKDIR /workspace/app
ENTRYPOINT ["/usr/local/bin/py2apk-entrypoint"]
